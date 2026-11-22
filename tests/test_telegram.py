from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock, patch

from newsbot.telegram import Telegram


class TelegramTests(IsolatedAsyncioTestCase):
    def setUp(self):
        with patch.dict(
            "os.environ", {"TELEGRAM_OWNER_ID": "123", "TELEGRAM_BOT_TOKEN": "fixture"}
        ):
            self.store = SimpleNamespace(vote=Mock(), item=Mock(return_value={"id": 5}))
            self.bot = Telegram(self.store)
        self.bot.call = AsyncMock(return_value={"message_id": 9})

    async def test_send(self):
        self.assertEqual(self.bot.channel({"urgent": 1}), 123)
        self.assertEqual(await self.bot.send(123, 5, "기사\nhttps://example.com/"), 9)
        kw = self.bot.call.await_args.kwargs
        self.assertEqual(kw["text"], "기사\nhttps://example.com/")
        self.assertEqual(
            kw["reply_markup"]["inline_keyboard"][0][0]["callback_data"], "news:5:up"
        )

    async def test_owner_only(self):
        q = {"from": {"id": 12}, "id": "button", "data": "news:5:up"}
        await self.bot.update({"callback_query": q})
        self.store.vote.assert_not_called()
        q["from"]["id"] = 123
        await self.bot.update({"callback_query": q})
        self.store.vote.assert_called_once_with(123, 5, "up")
