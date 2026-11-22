import asyncio
import copy
import os
import unittest
from unittest.mock import AsyncMock, patch

from test_flow import CFG, SBS

from newsbot.bot import translation_step
from newsbot.core import Item, Store, render
from newsbot.reports import activity, scorecard, source_report
from newsbot.telegram import Telegram


class Improvements(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        cfg = copy.deepcopy(CFG)
        cfg["settings"]["collect_all"] = True
        self.s = Store(":memory:", cfg)
        self.addCleanup(self.s.db.close)
        self.foreign = dict(
            SBS, id="wire", publisher="Wire", lang="en", rule="dedicated"
        )
        self.news = dict(SBS, rule="dedicated")
        self.url = "https://example.org/news/test1"
        for src in (self.foreign, self.news):
            self.s.ingest(src, [], {}, now=1)
        with patch.dict(
            os.environ, TELEGRAM_OWNER_ID="42", TELEGRAM_BOT_TOKEN="fixture"
        ):
            self.bot = Telegram(self.s)
        self.bot.call = AsyncMock(return_value={"message_id": 7})

    def add(self, src, title, t, uid="a"):
        self.s.ingest(src, [Item(uid, title, t, [self.url])], {}, now=t)

    async def test_translation_race(self):
        self.add(self.foreign, "Funding announcement", 2)
        started = asyncio.Event()
        finish = asyncio.Event()

        async def pause(*args):
            started.set()
            await finish.wait()
            return "자금 조달 발표"

        with patch("newsbot.bot.asyncio.to_thread", pause):
            translating = asyncio.create_task(translation_step(self.s))
            await started.wait()
            self.add(self.news, "자금 조달 발표", 3)
            finish.set()
            await translating
        self.assertEqual(self.s.item(1)["state"], "duplicate")
        self.assertEqual(self.s.item(2)["state"], "ready")
        self.assertIn("함께 보도 · Wire", render(self.s, self.s.item(2)))
        self.assertEqual(self.s.item(1)["related"], 2)
        self.assertIsNone(self.s.item(2)["related"])

    async def test_source_edit_and_hidden_rating(self):
        self.add(self.news, "자금 조달 발표", 2)
        with self.s.db:
            self.s.db.execute(
                "update items set state='sent',sent=5,channel='42',message='7' where id=1"
            )
        self.add(self.foreign, "Funding announcement", 3)
        q = {
            "id": "a",
            "from": {"id": 42},
            "data": "news:1:up",
            "message": {"chat": {"id": 42}, "message_id": 7},
        }
        await self.bot.update({"callback_query": q})
        await self.bot.edit_step()
        method = self.bot.call.await_args
        self.assertEqual(method.args, ("editMessageText",))
        self.assertIn("함께 보도 · Wire", method.kwargs["text"])
        self.assertEqual(method.kwargs["reply_markup"], {"inline_keyboard": []})
        self.assertIn("발송 1건 · 중복 1건", activity(self.s.db, 10)[0])
        self.assertIn("SBS", activity(self.s.db, 10)[1])
        self.assertIn("단독 0", source_report(self.s.db, 42, 10))
        await self.bot.update(
            {
                "message": {
                    "from": {"id": 42},
                    "chat": {"id": 42, "type": "private"},
                    "text": "/rate 1",
                }
            }
        )
        self.assertTrue(
            self.bot.call.await_args_list[-2].kwargs["reply_markup"]["inline_keyboard"]
        )
        q["data"] = "news:1:undo"
        await self.bot.update({"callback_query": q})
        self.assertIn("아직 비교 가능한 평가가 없습니다", scorecard(self.s.db, 42))

    def test_compare_without_lookahead(self):
        self.add(self.news, "CoreWeave 자금 조달", 2)
        self.s.vote(42, 1, "up")
        self.url += "2"
        self.add(self.news, "CRWV 회사채 발행 발표", 3, "b")
        snap = self.s.db.execute("select * from predictions where item=2").fetchone()
        self.assertNotEqual(snap["score"], snap["trial"])
        self.s.vote(42, 2, "down")
        self.assertIn("평가 2건", scorecard(self.s.db, 42))
        self.assertEqual(
            snap[:],
            self.s.db.execute("select * from predictions where item=2").fetchone()[:],
        )
        self.s.vote(42, 1, "undo")
        self.assertIn("평가 1건", scorecard(self.s.db, 42))

    def test_migrate_preexisting_blocked_story(self):
        self.add(self.foreign, "Funding announcement", 2)
        self.add(self.news, "자금 조달 발표", 3)
        with self.s.db:
            self.s.db.execute(
                "update items set state='translate',related=null where id=1"
            )
            self.s.db.execute("update items set state='duplicate',related=1 where id=2")
        self.s.recover_representatives(4)
        self.s.recover_representatives(4)
        self.assertEqual(self.s.item(1)["state"], "duplicate")
        self.assertEqual(self.s.item(2)["state"], "ready")

    async def test_bbc_regular_article_translated_before_send(self):
        from newsbot.bot import translation_step

        src = dict(self.foreign, rule="none", publisher="BBC")
        self.add(src, "New chip factory opens", 2)
        self.assertEqual(self.s.item(1)["state"], "translate")
        await self.bot.send_step()
        self.bot.call.assert_not_awaited()
        with patch("newsbot.bot.translate", return_value="새 반도체 공장 문 열어"):
            await translation_step(self.s)
        await self.bot.send_step()
        lines = self.bot.call.await_args.kwargs["text"].splitlines()
        self.assertEqual(lines[0], "새 반도체 공장 문 열어")
        self.assertTrue(lines[1].startswith("BBC · "))
        self.assertEqual(lines[-1], self.url)
        self.assertEqual(len(lines), 3)
