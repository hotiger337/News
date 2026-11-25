import copy
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import requests
from test_flow import CFG

from newsbot.__main__ import main
from newsbot.core import Item, Store, breaking
from newsbot.sources import bluesky

SOURCES = {s["id"]: s for s in CFG["telegram_sources"]}
AP = SOURCES["ap-bluesky"]


def post(number, text, **kwargs):
    uri = f"at://{AP['did']}/app.bsky.feed.post/{number}"
    record = {"text": text, "createdAt": f"2026-11-25T00:00:{number:02d}Z", **kwargs}
    return {
        "post": {
            "uri": uri,
            "author": {"did": AP["did"], "handle": AP["handle"]},
            "record": record,
        }
    }


def response(posts, cursor=None):
    r = Mock()
    r.json.return_value = {"feed": posts, "cursor": cursor}
    return r


class AlertSources(unittest.TestCase):
    def test_publisher_markers(self):
        for key in ("yna-latest", "jtbc-flash", "yonhapnews-tv"):
            self.assertTrue(breaking(SOURCES[key], Item("1", "[속보] 개표 종료", 1)))
            self.assertFalse(
                breaking(SOURCES[key], Item("2", "지난 속보를 되돌아보다", 2))
            )
        self.assertTrue(
            breaking(SOURCES["yonhap-en"], Item("1", "(URGENT) Vote ends", 1))
        )
        self.assertFalse(
            breaking(SOURCES["yonhap-en"], Item("2", "(LEAD) Vote ends", 2))
        )
        self.assertTrue(
            breaking(SOURCES["nyt-bluesky"], Item("1", "Breaking News: Vote ends", 1))
        )

    def test_bluesky_baseline_recovery_and_quiet_poll(self):
        s = Store(":memory:", copy.deepcopy(CFG))
        self.addCleanup(s.db.close)
        old = post(1, "BREAKING: First result")
        regular = post(2, "An ordinary headline about breaking news")
        link = "https://apnews.com/article/result"
        new = post(3, "BREAKING: Final result", embed={"external": {"uri": link}})
        repost = {
            **post(4, "BREAKING: Repost"),
            "reason": {"$type": "app.bsky.feed.defs#reasonRepost"},
        }
        reply = post(5, "BREAKING: Reply", reply={"root": {}})
        foreign = post(6, "BREAKING: Another author")
        foreign["post"]["author"] = {"did": "other", "handle": "other.example"}
        with patch(
            "newsbot.sources.requests.get", return_value=response([regular, old])
        ):
            baseline, cursor, gap = bluesky(AP, None)
        self.assertFalse(gap)
        self.assertEqual(len(baseline), 1)
        s.ingest(AP, baseline, cursor)
        self.assertEqual(s.item(1)["state"], "baseline")
        with patch(
            "newsbot.sources.requests.get",
            side_effect=[
                response([repost, reply, foreign, new], "next"),
                response([regular, old]),
            ],
        ) as get:
            items, checkpoint, gap = bluesky(AP, cursor)
        self.assertEqual(get.call_args.kwargs["params"]["cursor"], "next")
        self.assertFalse(gap)
        s.ingest(AP, items, checkpoint)
        self.assertEqual(s.db.execute("select count(*) from items").fetchone()[0], 2)
        self.assertEqual(s.item(2)["state"], "translate")
        self.assertEqual(items[0].links, [link])
        self.assertEqual(items[0].post, f"https://bsky.app/profile/{AP['did']}/post/3")
        with patch(
            "newsbot.sources.requests.get", return_value=response([new, regular, old])
        ):
            items, cursor, gap = bluesky(AP, checkpoint)
            s.ingest(AP, items, cursor, gap)
        self.assertFalse(gap)
        self.assertEqual(s.db.execute("select count(*) from items").fetchone()[0], 2)

    def test_no_matches_errors_and_missing_boundary_are_distinct(self):
        with patch(
            "newsbot.sources.requests.get", return_value=response([post(8, "Ordinary")])
        ):
            items, cursor, gap = bluesky(AP, None)
        self.assertEqual((items, gap), ([], ""))
        with (
            patch("newsbot.sources.requests.get", side_effect=requests.Timeout),
            self.assertRaises(requests.Timeout),
        ):
            bluesky(AP, cursor)
        with patch(
            "newsbot.sources.requests.get",
            return_value=response(
                [post(9, "BREAKING: New"), post(1, "BREAKING: Historic")]
            ),
        ):
            items, _, gap = bluesky(AP, cursor)
        self.assertIn("누락", gap)
        self.assertEqual([i.title for i in items], ["BREAKING: New"])

    def test_publisher_identity_and_utf8_link(self):
        text = "BREAKING: 서울 소식 www.example.com/2026/11/..."
        offset = len("BREAKING: 서울 소식 ".encode())
        url = "https://apnews.com/article/original"
        item = post(
            1,
            text,
            facets=[
                {
                    "index": {"byteStart": offset, "byteEnd": len(text.encode())},
                    "features": [{"$type": "app.bsky.richtext.facet#link", "uri": url}],
                }
            ],
        )
        with patch("newsbot.sources.requests.get", return_value=response([item])):
            items, _, _ = bluesky(AP, None)
            self.assertEqual(items[0].title, "BREAKING: 서울 소식")
            self.assertEqual(items[0].links, [url])
            item["post"]["author"]["handle"] = "other.example"
            with self.assertRaises(ValueError):
                bluesky(AP, None)

    def test_only_telegram_service_loads_additional_sources(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.dict(
                os.environ,
                NEWS_DB=str(Path(tmp) / "test.sqlite"),
                NEWS_CONFIG="sources.toml",
                DISCORD_TOKEN="fixture",
                TELEGRAM_BOT_TOKEN="",
            ),
            patch("newsbot.__main__.Store") as store,
        ):
            with (
                patch("sys.argv", ["newsbot", "run"]),
                patch("newsbot.__main__.NewsBot"),
            ):
                main()
            self.assertTrue(
                SOURCES.keys().isdisjoint(
                    s["id"] for s in store.call_args.args[1]["sources"]
                )
            )
            with (
                patch("sys.argv", ["newsbot", "run-telegram"]),
                patch("newsbot.telegram.Telegram") as tg,
            ):
                tg.return_value.run = AsyncMock()
                main()
            self.assertTrue(
                SOURCES.keys() <= {s["id"] for s in store.call_args.args[1]["sources"]}
            )
