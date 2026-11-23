import random
import sqlite3
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from newsbot import social


class SocialReport(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.db.row_factory = sqlite3.Row
        self.db.execute("create table interests(name text)")
        self.db.execute("insert into interests values('반도체')")
        cfg = {"social": {"regions": ["KR"], "hours_kst": [12, 20]},
               "topics": {"반도체": ["GPU"]}}
        self.channel = SimpleNamespace(send=AsyncMock())
        self.bot = SimpleNamespace(store=SimpleNamespace(cfg=cfg, db=self.db), interest_id=42,
                                   transport=SimpleNamespace(target=AsyncMock(return_value=self.channel)))
        self.yt = [("YouTube", f"https://example.org/{i}", f"음악 {i}", "", False)
                   for i in range(8)]
        self.rd = [("Reddit", f"https://example.org/r{i}", f"GPU {i}", "", False)
                   for i in range(8)]

    def test_overlap_and_seen(self):
        outside, related = social.select(
            self.yt + [(*self.yt[0][:4], True)] + self.rd, ["반도체"],
            self.bot.store.cfg, {self.yt[1][1]}, random.Random(1))
        self.assertEqual((len(outside), len(related)), (2, 2))
        self.assertTrue(all(item[1] not in {self.yt[0][1], self.yt[1][1]} for item in outside))
        self.assertEqual({item[0] for item in related}, {"YouTube", "Reddit"})

    async def test_two_daily_slots(self):
        def verified(groups, cfg):
            return [[(*item, "https://apnews.com/article/test") for item in group[:2]]
                    for group in groups]

        with patch.object(social, "verify", side_effect=verified), patch.object(social, "datetime") as clock, patch.object(
            social, "youtube", return_value=self.yt
        ) as youtube, patch.object(social, "reddit", return_value=self.rd):
            for hour in [11, 12, 12, 19, 20, 20]:
                clock.now.return_value = datetime(2026, 11, 23, hour, tzinfo=social.KST)
                await social.report(self.bot)
        self.assertEqual(youtube.call_count, 2)
        self.assertEqual(self.channel.send.await_count, 2)
        urls = set()
        for call in self.channel.send.await_args_list:
            links = {line.split(" · ")[0] for line in call.args[0].splitlines() if line.startswith("원문")}
            self.assertEqual(len(links), 4)
            self.assertFalse(links & urls)
            urls.update(links)

    async def test_unverified_items_are_not_delivered(self):
        with patch.object(social, "datetime") as clock, patch.object(
            social, "youtube", return_value=self.yt
        ), patch.object(social, "reddit", return_value=[]), patch.object(
            social, "verify", return_value=[[], []]
        ) as verify:
            clock.now.return_value = datetime(2026, 11, 23, 12, tzinfo=social.KST)
            await social.report(self.bot)
            await social.report(self.bot)
        self.channel.send.assert_not_awaited()
        self.assertEqual(verify.call_count, 1)

    def test_only_allowlisted_retrieved_evidence_passes(self):
        import json
        from newsbot.verification import verify

        evidence = "https://apnews.com/article/story"
        groups = [[item[:3] for item in self.yt[:3]], []]
        other = "https://apnews.com.fake.example/story"
        result = [{"index": i, "url": url, "fact": "확인된 사실"} for i, url in
                  enumerate([evidence, evidence + "-invented", other])]
        response = {"status": "completed", "output": [
            {"type": "web_search_call", "status": "completed",
             "action": {"sources": [{"url": evidence}, {"url": other}]}},
            {"type": "message", "content": [{"type": "output_text",
             "text": json.dumps({"verified": result})}]},
        ]}
        with patch("newsbot.verification.required", return_value="mock"), patch(
            "newsbot.verification.requests.post"
        ) as post:
            post.return_value.json.return_value = response
            accepted = verify(groups, {"verification_domains": ["apnews.com"],
                                       "verification_model": "test"})
        self.assertEqual([row[1] for row in accepted[0]], [self.yt[0][1]])
