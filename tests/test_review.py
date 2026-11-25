import asyncio
import copy
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

from test_flow import CFG, SBS

from newsbot import review
from newsbot.core import KST, Item, Store
from newsbot.telegram import Telegram


class TelegramAlerts(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "telegram.sqlite"
        self.cfg = copy.deepcopy(CFG)
        self.cfg["settings"]["collect_all"] = True
        self.cfg["sources"] = []  # No external pollers in this service test.
        self.noon = int(datetime(2026, 11, 25, 12, tzinfo=KST).timestamp())
        self.store = Store(self.path, self.cfg)
        self.addCleanup(lambda: self.store.db.close())
        env = patch.dict(
            os.environ,
            TELEGRAM_OWNER_ID="42",
            TELEGRAM_BOT_TOKEN="fixture",
            TELEGRAM_BREAKING_CHAT_ID="52",
            NEWS_SITE_TOKEN="",
        )
        env.start()
        self.addCleanup(env.stop)
        self.store.db.executescript(review.SCHEMA)
        self.store.ingest(SBS, [], {}, now=self.noon - 60)
        titles = [
            "일반 기사 A",
            "일반 기사 B",
            "일반 기사 C",
            "일반 기사 D",
            "일반 기사 E",
            "[속보] 경기 결과",
        ]
        self.store.ingest(
            SBS,
            [Item(str(i), t, self.noon - 1) for i, t in enumerate(titles)],
            {},
            now=self.noon,
        )
        with self.store.db as db:
            db.execute(
                "insert into reviews(slot,state) values(10,'pending'),(20,'sending'),(30,'sent')"
            )
            db.executemany(
                "insert into review_items values(?,?,?)",
                [(10, 0, 1), (20, 0, 2), (30, 0, 3), (30, 1, 4)],
            )
            db.execute("update items set state='review_queued' where id in (1,2)")
            db.execute("update items set state='reviewed',sent=31 where id in (3,4)")
            db.execute("update items set state='retry' where id=5")

    async def one_service_cycle(self, now):
        bot = Telegram(self.store)
        bot.call = AsyncMock(
            side_effect=lambda method, **kw: {
                "getWebhookInfo": {"url": ""},
                "getUpdates": [],
                "sendMessage": {"message_id": 9},
            }[method]
        )
        # Let every scheduled worker run one iteration, then stop the service.
        with (
            patch("newsbot.telegram.time.time", return_value=now),
            patch("newsbot.telegram.asyncio.sleep", side_effect=asyncio.CancelledError),
            patch("newsbot.telegram.archive_step", new_callable=AsyncMock),
            self.assertRaises(asyncio.CancelledError),
        ):
            await bot.run()
        return [
            c.kwargs for c in bot.call.await_args_list if c.args == ("sendMessage",)
        ]

    async def test_noon_restart_evening_only_delivers_breaking(self):
        sent = await self.one_service_cycle(self.noon)
        self.assertEqual(len(sent), 1)
        self.assertIn("[속보] 경기 결과", sent[0]["text"])
        self.assertEqual(sent[0]["chat_id"], 52)
        self.store.db.close()
        self.store = Store(self.path, self.cfg)
        self.assertEqual(await self.one_service_cycle(self.noon + 8 * 3600), [])
        self.assertEqual(
            dict(self.store.db.execute("select slot,state from reviews")),
            {10: "cancelled", 20: "uncertain", 30: "sent"},
        )
        self.assertIsNone(self.store.item(1)["sent"])
        self.assertEqual(self.store.item(3)["sent"], 31)
        self.assertIsNone(self.store.item(5)["sent"])
        self.assertEqual(self.store.item(6)["state"], "sent")

    async def test_old_commands_do_not_send_general_news(self):
        bot = Telegram(self.store)
        bot.call = AsyncMock()
        with self.store.db as db:
            db.execute("update items set state='uncertain' where id=5")
        for cmd in ("/review", "/review 10", "/held", "/held 1", "/retry 5", "/status"):
            await bot.update(
                {
                    "message": {
                        "from": {"id": 42},
                        "chat": {"type": "private", "id": 42},
                        "text": cmd,
                    }
                }
            )
            reply = bot.call.await_args.kwargs
            self.assertNotIn("일반 기사 A", reply["text"])
            self.assertNotIn("reply_markup", reply)
        self.assertIn("속보만", reply["text"])
        self.assertEqual(self.store.item(5)["state"], "uncertain")

    async def test_previously_delivered_review_still_rates_only_selected_item(self):
        review.retire_schedule(self.store.db)
        bot = Telegram(self.store)
        bot.call = AsyncMock()
        key = review.payload(self.store.db, 42, 30, 0)["reply_markup"][
            "inline_keyboard"
        ][1][0]["callback_data"]
        cb = {
            "callback_query": {
                "id": "b",
                "from": {"id": 7},
                "data": key,
                "message": {"chat": {"id": 42}, "message_id": 5},
            }
        }
        await bot.update(cb)
        self.assertEqual(
            self.store.db.execute("select count(*) from votes").fetchone()[0], 0
        )
        cb["callback_query"]["from"]["id"] = 42
        await bot.update(cb)
        self.assertEqual(
            [tuple(r) for r in self.store.db.execute("select item,content from votes")],
            [(4, 1)],
        )
        cb["callback_query"]["data"] = bot.call.await_args.kwargs["reply_markup"][
            "inline_keyboard"
        ][1][0]["callback_data"]
        await bot.update(cb)
        self.assertEqual(
            self.store.db.execute("select count(*) from votes").fetchone()[0], 0
        )
