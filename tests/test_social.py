import json
import random
import sqlite3
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from newsbot import social, verification


class SocialReport(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""create table interests(name text);
            insert into interests values('반도체');
            create table items(id integer primary key,source text,data text,found integer);""")
        cfg = {"social": {"hours_kst": [12, 20]}, "sources": [],
               "topics": {"반도체": ["GPU"]}}
        self.channel = SimpleNamespace(send=AsyncMock())
        self.bot = SimpleNamespace(store=SimpleNamespace(cfg=cfg, db=self.db), interest_id=42,
                                   transport=SimpleNamespace(target=AsyncMock(return_value=self.channel)))
        self.entries = [(platform, f"https://example.org/{platform}{i}", title, "",
                         "https://bbc.com/test") for platform, title in
                        [("Reddit", "GPU"), ("YouTube", "음악")] for i in range(6)]

    async def test_two_daily_slots_no_repeats(self):
        with patch.object(social, "datetime") as clock, patch.object(
            social, "collect", return_value=self.entries
        ) as collect:
            for hour in [11, 12, 12, 19, 20, 20]:
                clock.now.return_value = datetime(2026, 11, 23, hour, tzinfo=social.KST)
                await social.report(self.bot)
        self.assertEqual(collect.call_count, 2)
        self.assertEqual(self.channel.send.await_count, 2)
        urls = set()
        for call in self.channel.send.await_args_list:
            links = {line.split(" · ")[0] for line in call.args[0].splitlines() if line.startswith("원문")}
            self.assertEqual(len(links), 4)
            self.assertFalse(links & urls)
            urls.update(links)

    async def test_empty_slot(self):
        with patch.object(social, "datetime") as clock, patch.object(social, "collect", return_value=[]):
            clock.now.return_value = datetime(2026, 11, 23, 12, tzinfo=social.KST)
            await social.report(self.bot)
        self.channel.send.assert_not_awaited()

    def test_collector_requires_exact_citation_and_affirmative_review(self):
        article = "https://bbc.com/story"
        video = "https://www.youtube.com/watch?v=1234567890A"
        post = "https://www.reddit.com/r/news/comments/abc123/title/"
        review = {"@type": "ClaimReview", "reviewRating": {"alternateName": "True"},
                  "itemReviewed": {"appearance": {"url": post}}, "claimReviewed": "GPU fact"}
        feed = b"""<feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015">
        <entry><title>A video</title><yt:channelId>channel</yt:channelId>
        <link href="https://www.youtube.com/watch?v=1234567890A"/></entry></feed>"""
        def page(url, domains):
            if url == article:
                return url, markup.encode()
            self.assertTrue(url.startswith("https://www.youtube.com/feeds/"))
            return url, feed
        with patch.object(verification, "read_page", side_effect=page):
            for rating, expected in [("True", {"Reddit", "YouTube"}), ("Mostly False", {"YouTube"})]:
                review["reviewRating"]["alternateName"] = rating
                markup = (f'<article><a href="{video}">video</a><a href="{post}">post</a></article>'
                          + '<script type="application/ld+json">' + json.dumps(review) + '</script>')
                found = verification.collect([(article, "headline")], ["channel"], ["bbc.com"], [])
                self.assertEqual({row[0] for row in found}, expected)
            markup = f'<footer><a href="{video}">nav</a></footer><main><a href="{post}">rumor</a></main>'
            self.assertEqual(verification.collect([(article, "headline")], ["channel"], ["bbc.com"], []), [])

    def test_untrusted_domain(self):
        self.assertFalse(verification.safe_page("https://bbc.com.fake.example/story", ["bbc.com"]))
        self.assertIsNone(verification.social_url("https://reddit.com.evil.test/comments/abc/"))
