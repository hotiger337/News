import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from test_flow import CFG, SBS, Transport

from newsbot.bot import Rating, buttons, poll, translation_step
from newsbot.core import Item, Store, breaking, render
from newsbot.delivery import deliver
from newsbot.sources import fx, mail_item, rss

X = next(s for s in CFG["sources"] if s["id"] == "bbcx")
UTC = timezone.utc


class Operations(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "bot.sqlite"
        self.s = self.open()
        self.now = int(datetime(2026, 1, 1, 15, 30, tzinfo=UTC).timestamp())

    def open(self):
        s = Store(self.path, CFG)
        self.addCleanup(s.db.close)
        return s

    def ingest(self, source, items):
        self.s.ingest(source, items, {}, now=self.now)

    async def test_translation_date_order_and_source_isolation(self):
        self.ingest(X, [])
        article = "https://bbc.co.uk/news/articles/123"
        post = "https://x.com/BBCBreaking/status/123"
        self.ingest(
            X, [Item("123", "Company considers $30m", self.now, [article], post)]
        )
        with patch("newsbot.bot.translate", side_effect=TimeoutError):
            await translation_step(self.s)
        self.assertEqual(self.s.item(1)["state"], "translate")
        with patch.dict("newsbot.bot.ADAPTERS", {"rss": Mock(side_effect=ValueError)}):
            await poll(self.s, SBS)
        self.assertEqual(
            self.s.db.execute("select error from sources where id='sbs'").fetchone()[0],
            "ValueError",
        )
        self.ingest(SBS, [])
        self.ingest(SBS, [Item("s", "[속보] 결승", self.now)])
        t = Transport()
        await deliver(self.s, self.s.item(2), t)
        self.s.db.execute("update items set due=0 where id=1")
        with patch("newsbot.bot.translate", return_value="기업이 3천만 달러를 검토 중"):
            await translation_step(self.s)
        await deliver(self.s, self.s.item(1), t)
        lines = t.messages[1].splitlines()
        self.assertEqual(
            lines[:4],
            [
                "[해외 속보] BBC · @BBCBreaking",
                "기업이 3천만 달러를 검토 중",
                "Company considers $30m",
                "2026-01-02 00:30 · 한국시간",
            ],
        )
        self.assertEqual(lines[-2:], ["<" + post + ">", "<" + article + ">"])
        self.assertIsNotNone(self.s.item(1)["translated"])

    async def test_restart_and_ambiguous_send(self):
        self.ingest(SBS, [])
        item = Item("a", "[속보] 확인", self.now)
        self.ingest(SBS, [item])
        t = Transport()

        async def lost(channel, n, body):
            t.messages[n] = body
            raise TimeoutError

        with patch.object(t, "send", lost):
            await deliver(self.s, self.s.item(1), t)
        self.assertEqual(self.s.item(1)["state"], "uncertain")
        s = self.open()
        s.ingest(SBS, [item], {})
        with patch.object(t, "send", AsyncMock(side_effect=AssertionError)) as retry:
            await deliver(s, s.item(1), t)
            retry.assert_not_awaited()
        self.assertEqual(s.item(1)["state"], "sent")
        self.assertIsNone(s.item(2))
        button = buttons(1).children[0]
        restored = await Rating.from_custom_id(
            None,
            None,
            Rating.__discord_ui_compiled_template__.fullmatch(button.custom_id),
        )
        i = SimpleNamespace(
            client=SimpleNamespace(store=s, allowed=lambda i: True),
            user=SimpleNamespace(id=7),
            response=SimpleNamespace(send_message=AsyncMock()),
        )
        await restored.callback(i)
        self.assertEqual(
            s.db.execute("select item,content from votes").fetchone()[:], (1, 1)
        )

    async def test_digest_each_item_and_timing(self):
        self.ingest(SBS, [])
        self.ingest(SBS, [Item(str(i), "반도체 " + str(i), self.now) for i in range(6)])
        self.ingest(SBS, [Item("q", "데이터센터 자료", self.now - 100)])
        self.s.vote(7, 2, "up")
        before = self.s.score(self.s.item(3), self.now)[0]
        self.s.vote(7, 2, "batch")
        score, _, batch = self.s.score(self.s.item(3), self.now)
        self.assertEqual(before, score)
        self.assertTrue(batch)
        self.s.vote(7, 2, "undo")
        evening = int(datetime(2026, 1, 2, 11, 0, tzinfo=UTC).timestamp())
        self.s.digest(evening - 1)
        self.assertFalse(
            self.s.db.execute("select 1 from items where state='ready'").fetchone()
        )
        self.s.digest(evening)
        self.s.digest(evening)
        ready = list(
            self.s.db.execute("select * from items where state='ready' order by id")
        )
        self.assertEqual(len(ready), 5)
        self.assertEqual(
            self.s.db.execute(
                "select count(*) from rankings where run='2026-01-02'"
            ).fetchone()[0],
            7,
        )
        t = Transport()
        for r in ready:
            await deliver(self.s, r, t)
        second = buttons(ready[1]["id"]).children[1]
        i = SimpleNamespace(
            client=SimpleNamespace(store=self.s, allowed=lambda i: True),
            user=SimpleNamespace(id=7),
            response=SimpleNamespace(send_message=AsyncMock()),
        )
        await second.callback(i)
        self.assertEqual(
            list(self.s.db.execute("select item,content from votes"))[0][:],
            (ready[1]["id"], -1),
        )
        i.client.allowed = lambda i: False
        await buttons(ready[0]["id"]).children[0].callback(i)
        self.assertEqual(
            self.s.db.execute("select count(*) from votes").fetchone()[0], 1
        )

    async def test_correction_baseline_cross_channel_and_related(self):
        url = "https://a.example/doc/1"
        self.ingest(SBS, [Item("old", "[속보] 과거", 1)])
        self.assertEqual(self.s.item(1)["state"], "baseline")
        self.ingest(SBS, [Item("1", "[속보] 10억 검토", 2, [url])])
        await deliver(self.s, self.s.item(2), Transport())
        self.ingest(SBS, [Item("1", "[정정] 12억 확정", 3, [url])])
        self.assertTrue(self.s.item(3)["urgent"])
        self.assertIn("이전: [속보] 10억 검토", render(self.s, self.s.item(3)))
        x = dict(X, publisher="SBS")
        self.ingest(x, [])
        self.ingest(
            x, [Item("100", "다른 문구", 4, [url], "https://x.com/SBS/status/100")]
        )
        self.assertIsNone(self.s.item(4))
        self.ingest(X, [])
        self.ingest(X, [Item("200", "Other outlet", 4, [url])])
        self.assertEqual(self.s.item(4)["related"], 3)
        self.ingest(
            X, [Item("201", "Other outlet", 5, ["https://example.com/unrelated"])]
        )
        self.assertIsNone(self.s.item(5)["related"])

    def test_email_exact_sender(self):
        raw = b"From: WP <alerts@example.org>\r\nDate: Thu, 01 Jan 2026 00:00:00 -0500\r\nSubject: Vote concludes\r\nMessage-ID: <1@test>\r\n\r\nhttps://www.washingtonpost.com/world/2026/01/01/story/"
        self.assertIsNone(mail_item(raw, "other@example.org", 1))
        i = mail_item(raw, "alerts@example.org", 1)
        self.assertEqual(len(i.links), 1)
        self.assertTrue(breaking(dict(rule="dedicated"), i))

    def test_rss_adapter_to_delivery_queue(self):
        xml = b'<rss version="2.0"><channel><title>News</title><item><guid>1</guid><title>[\xec\x86\x8d\xeb\xb3\xb4] A</title><link>https://test.example/a</link><pubDate>Thu, 01 Jan 2026 00:00:00 GMT</pubDate></item><item><guid>2</guid><title>newsflash</title><pubDate>Thu, 01 Jan 2026 00:00:00 GMT</pubDate></item></channel></rss>'
        with patch("newsbot.sources.requests.get", return_value=Mock(content=xml)):
            entries, checkpoint, gap = rss(SBS, {"ids": ["previous"]})
        self.assertTrue(gap)
        self.ingest(SBS, [])
        self.s.ingest(SBS, entries, checkpoint, gap)
        self.assertEqual(
            [self.s.item(n)["state"] for n in (1, 2)], ["ready", "candidate"]
        )

    def test_fx_protocol(self):
        def page(ids, cursor):
            results = [
                dict(
                    type="status",
                    id=str(i),
                    created_timestamp=i,
                    text="body",
                    url=f"https://x.com/BBCBreaking/status/{i}",
                    author=dict(screen_name="BBCBreaking"),
                )
                for i in ids
            ]
            return Mock(
                status_code=200,
                json=lambda: dict(
                    code=200, results=results, cursor=dict(bottom=cursor)
                ),
            )

        old = {"id": "5", "ts": 5}
        with patch(
            "newsbot.sources.requests.get",
            side_effect=[page([9, 8], "c"), page([7, 6, 5], None)],
        ) as get:
            items, checkpoint, gap = fx(X, old)
        self.assertEqual([x.uid for x in items], ["9", "8", "7", "6"])
        self.assertEqual(checkpoint["id"], "9")
        self.assertFalse(gap)
        self.assertEqual(get.call_args_list[1].kwargs["params"], {"cursor": "c"})
        with patch("newsbot.sources.requests.get", return_value=Mock(status_code=204)):
            self.assertEqual(fx(X, old), ([], old, ""))
        with patch("newsbot.sources.requests.get", return_value=page([8], None)):
            self.assertTrue(fx(X, old)[2])

    async def test_same_headline_changed_amount(self):
        self.ingest(SBS, [])
        first = Item(
            "doc", "[속보] 투자 발표", self.now, detail="투자액은 10억 원이다."
        )
        self.ingest(SBS, [first])
        await deliver(self.s, self.s.item(1), Transport())
        changed = Item("doc", first.title, self.now, detail="투자액은 12억 원이다.")
        self.ingest(SBS, [changed])
        self.ingest(SBS, [changed])
        message = render(self.s, self.s.item(2))
        self.assertIn("12억", message)
        self.assertIn("10억", message)
        self.assertIsNone(self.s.item(3))

    async def test_normal_entry_from_rss_to_rank(self):
        from newsbot.bot import poll

        def document(body):
            return (
                "<rss version='2.0'><channel><title>SBS</title>"
                + body
                + "</channel></rss>"
            ).encode()

        def entry(n, title):
            return f"<item><guid>{n}</guid><title>{title}</title><pubDate>Thu, 01 Jan 2026 15:30:00 GMT</pubDate><link>https://example.com/{n}</link></item>"

        old = entry(1, "과거")
        feed = (
            old
            + entry(2, "[속보] 코어위브 자금 조달")
            + entry(3, "CRWV 회사채")
            + entry(4, "다른 기업")
        )
        with patch(
            "newsbot.sources.requests.get",
            side_effect=[Mock(content=document(old)), Mock(content=document(feed))],
        ):
            await poll(self.s, SBS)
            await poll(self.s, SBS)
        self.assertEqual(self.s.item(1)["state"], "baseline")
        channel = Transport()
        await deliver(self.s, self.s.item(2), channel)
        before = self.s.score(self.s.item(3), self.now)[0]
        click = SimpleNamespace(
            client=SimpleNamespace(store=self.s, allowed=lambda i: True),
            user=SimpleNamespace(id=9),
            response=SimpleNamespace(send_message=AsyncMock()),
        )
        await buttons(2).children[0].callback(click)
        self.assertGreater(self.s.score(self.s.item(3), self.now)[0], before)
        self.assertGreater(
            self.s.score(self.s.item(3), self.now)[0],
            self.s.score(self.s.item(4), self.now)[0],
        )

    def test_translation_preserves_figures(self):
        from newsbot.sources import translate

        def response(url, **kwargs):
            markup = kwargs["json"][0]["Text"]
            self.assertIn('<span class="notranslate">$30 million</span>', markup)
            self.assertIn("&amp;", markup)
            return Mock(
                json=lambda: [{"translations": [{"text": "검토 중 · " + markup}]}]
            )

        with (
            patch.dict("os.environ", {"TRANSLATOR_KEY": "fixture"}),
            patch("newsbot.sources.requests.post", side_effect=response),
        ):
            result = translate(
                "CoreWeave & partners consider $30 million", "en", ["CoreWeave"]
            )
        self.assertIn("$30 million", result)
        self.assertIn("CoreWeave &", result)
