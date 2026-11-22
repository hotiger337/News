import copy
import os
import unittest
from datetime import datetime
from unittest.mock import AsyncMock, patch

from test_flow import CFG, SBS

from newsbot import review
from newsbot.core import KST, Item, Store
from newsbot.telegram import BotAPIError, Telegram


class ReviewTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        cfg = copy.deepcopy(CFG)
        cfg["settings"]["collect_all"] = True
        self.s = Store(":memory:", cfg)
        self.addCleanup(self.s.db.close)
        self.db = self.s.db
        self.db.executescript(review.SCHEMA)
        self.noon = int(datetime(2026, 11, 22, 12, tzinfo=KST).timestamp())
        self.s.ingest(SBS, [], {}, now=self.noon - 120)
        with patch.dict(
            os.environ, TELEGRAM_OWNER_ID="42", TELEGRAM_BOT_TOKEN="fixture"
        ):
            self.bot = Telegram(self.s)
        self.bot.call = AsyncMock(return_value={"message_id": 5})
        review.prepare(self.db, self.noon - 60)
        self.s.ingest(
            SBS,
            [Item(str(i), f"샘플 기사 {i}", self.noon - 10) for i in range(6)]
            + [Item("u", "[속보] 발표", self.noon - 1)],
            {},
            now=self.noon - 1,
        )

    async def step(self, t):
        with patch("newsbot.telegram.time.time", return_value=t):
            await self.bot.review_step()

    async def test_slot_delivery_and_per_article_rating(self):
        await self.step(self.noon - 1)
        self.bot.call.assert_not_awaited()
        await self.bot.send_step()
        self.assertIn("속보", self.bot.call.await_args.kwargs["text"])
        self.bot.call.reset_mock()
        await self.step(self.noon)
        await self.step(self.noon + 1)
        self.bot.call.assert_awaited_once()
        body = self.bot.call.await_args.kwargs
        self.assertIn("6건 · 1/2", body["text"])
        key = body["reply_markup"]["inline_keyboard"][1][0]["callback_data"]
        item = int(key.split(":")[-1])
        cb = {
            "callback_query": {
                "id": "b",
                "data": key,
                "from": {"id": 42},
                "message": {"chat": {"id": 42}, "message_id": 5},
            }
        }
        await self.bot.update(cb)
        self.assertEqual(
            [tuple(r) for r in self.db.execute("select item,content from votes")],
            [(item, 1)],
        )
        buttons = self.bot.call.await_args.kwargs["reply_markup"]["inline_keyboard"]
        self.assertEqual(len(buttons[1]), 1)
        self.assertEqual(len(buttons[0]), 2)
        cb["callback_query"]["data"] = buttons[1][0]["callback_data"]
        await self.bot.update(cb)
        self.assertEqual(self.db.execute("select count(*) from votes").fetchone()[0], 0)
        cb["callback_query"]["data"] = buttons[-1][-1]["callback_data"]
        await self.bot.update(cb)
        self.assertIn("2/2", self.bot.call.await_args.kwargs["text"])
        self.s.ingest(
            SBS, [Item("e", "저녁에 볼 기사", self.noon + 1)], {}, now=self.noon + 1
        )
        self.bot.call.reset_mock()
        await self.step(self.noon + 8 * 3600)
        self.assertIn("저녁 뉴스 리뷰", self.bot.call.await_args.kwargs["text"])
        self.assertIn("1건", self.bot.call.await_args.kwargs["text"])

    async def test_unknown_delivery_is_not_repeated(self):
        self.bot.call.side_effect = BotAPIError("connection")
        await self.step(self.noon)
        await self.step(self.noon + 30)
        self.bot.call.assert_awaited_once()
        self.assertEqual(
            self.db.execute("select state from reviews").fetchone()[0], "uncertain"
        )

    async def test_owner_only(self):
        await self.step(self.noon)
        key = self.bot.call.await_args.kwargs["reply_markup"]["inline_keyboard"][0][0][
            "callback_data"
        ]
        await self.bot.update(
            {"callback_query": {"id": "b", "from": {"id": 7}, "data": key}}
        )
        self.assertEqual(self.db.execute("select count(*) from votes").fetchone()[0], 0)
