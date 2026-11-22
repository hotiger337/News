"""Personal Telegram Bot API client. All credentials stay on the VM."""

import asyncio
import json
import logging
import os
import re
import secrets
import time
from datetime import datetime
from getpass import getpass

import requests

from .bot import LABELS, poll, translation_step
from .core import KST, render
from .delivery import deliver
from .sources import required


class BotAPIError(Exception):
    def __init__(self, status):
        self.status = status
        super().__init__(f"Telegram API {status}")


class API:
    def __init__(self, token):
        self.token = token

    def call(self, method, **params):
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{self.token}/{method}",
                json=params,
                timeout=(8, 35),
            )
            result = r.json()
        except (requests.RequestException, ValueError):
            raise BotAPIError("connection") from None
        if not result.get("ok"):
            raise BotAPIError(result.get("error_code", r.status_code))
        return result["result"]


def identify():
    api = API(
        os.getenv("TELEGRAM_BOT_TOKEN")
        or getpass("BotFather 토큰 (화면에 표시되지 않음): ").strip()
    )
    bot = api.call("getMe")
    if api.call("getWebhookInfo")["url"]:
        raise SystemExit(
            "이 봇에는 웹훅이 연결돼 있습니다. 기존 서비스 용도를 먼저 확인하세요."
        )
    code = secrets.token_hex(8)
    print(
        f"텔레그램에서 https://t.me/{bot['username']} 을 열고 개인 대화에 /start {code} 를 보내세요.",
        flush=True,
    )
    offset = 0
    while True:
        for update in api.call(
            "getUpdates",
            offset=offset,
            timeout=20,
            allowed_updates=["message", "callback_query"],
        ):
            offset = update["update_id"] + 1
            msg = update.get("message", {})
            if (
                msg.get("chat", {}).get("type") == "private"
                and msg.get("text") == "/start " + code
            ):
                print("TELEGRAM_OWNER_ID=" + str(msg["from"]["id"]))
                print("개인 대화 설정에서는 채팅 ID 두 칸을 비워두세요.")
                return


class Telegram:
    def __init__(self, store):
        self.store = store
        self.api = API(required("TELEGRAM_BOT_TOKEN"))
        self.owner = int(required("TELEGRAM_OWNER_ID"))
        self.breaking = int(os.getenv("TELEGRAM_BREAKING_CHAT_ID") or self.owner)
        self.interest = int(os.getenv("TELEGRAM_INTEREST_CHAT_ID") or self.breaking)

    def channel(self, row):
        return self.breaking if row["urgent"] else self.interest

    async def call(self, method, **kwargs):
        return await asyncio.to_thread(self.api.call, method, **kwargs)

    async def send(self, channel, n, body):
        keys = [
            {"text": label, "callback_data": f"news:{n}:{act}"}
            for act, label in LABELS.items()
        ]
        msg = await self.call(
            "sendMessage",
            chat_id=channel,
            text=body.replace("\n<https://", "\nhttps://")
            .replace("\n<http://", "\nhttp://")
            .replace(">\n", "\n")
            .removesuffix(">"),
            reply_markup={"inline_keyboard": [keys[:2], keys[2:]]},
            link_preview_options={"is_disabled": True},
        )
        return msg["message_id"]

    async def update(self, u):
        callback = u.get("callback_query")
        if callback:
            response = "등록 사용자 전용입니다."
            if callback["from"]["id"] == self.owner:
                m = re.fullmatch(
                    r"news:(\d+):(up|down|batch|undo)", callback.get("data", "")
                )
                if m and self.store.item(int(m[1])):
                    self.store.vote(self.owner, int(m[1]), m[2])
                    response = (
                        (
                            "묶음 선호를 기록했습니다. 전체 수신 중에는 계속 즉시 발송합니다."
                            if self.store.cfg["settings"].get("collect_all")
                            else "관심 자료만 묶어서 받습니다. 속보는 즉시 발송합니다."
                        )
                        if m[2] == "batch"
                        else "평가를 반영했습니다."
                    )
            await self.call(
                "answerCallbackQuery", callback_query_id=callback["id"], text=response
            )
            return
        msg = u.get("message", {})
        if (
            msg.get("chat", {}).get("type") != "private"
            or msg.get("from", {}).get("id") != self.owner
        ):
            return
        cmd, _, arg = msg.get("text", "").strip().partition(" ")
        arg = arg.strip()
        db = self.store.db
        if cmd in ("/start", "/help"):
            reply = "/status 상태 · /recent 최근 속보 · /interests 관심사 · /add 이름 · /remove 이름 · /held 보류 후보 · /held 번호 평가 · /retry 번호 응답 유실 건 재발송"
        elif cmd == "/status":
            lines = [
                "전체 수신 · 중복 정리"
                if self.store.cfg["settings"].get("collect_all")
                else "추천 수신"
            ]
            for src in self.store.cfg["sources"]:
                r = db.execute(
                    "select * from sources where id=?", (src["id"],)
                ).fetchone()
                stamp = (
                    datetime.fromtimestamp(r["success"], KST).strftime("%m-%d %H:%M")
                    if r and r["success"]
                    else "없음"
                )
                lines.append(
                    f"{src['id']} · 최근 성공 {stamp} · "
                    + (
                        " / ".join(filter(None, [r["error"], r["gap"]])) or "정상"
                        if r
                        else src.get("verification", "대기")
                    )
                )
            lines.extend(
                f"{s}: {count}"
                for s, count in db.execute(
                    "select state,count(*) from items where state in ('ready','translate','retry','sending','uncertain') group by state"
                )
            )
            lines.extend(
                f"응답 확인 필요 #{n}"
                for (n,) in db.execute(
                    "select id from items where state in ('sending','uncertain') order by id desc limit 10"
                )
            )
            lines.extend(
                f"#{r['id']} 게시→발송 {r['sent'] - json.loads(r['data'])['published']}초"
                for r in db.execute(
                    "select * from items where sent is not null order by sent desc limit 3"
                )
            )
            reply = "\n".join(lines)
        elif cmd == "/interests":
            reply = (
                ", ".join(r[0] for r in db.execute("select name from interests"))
                or "등록된 관심사가 없습니다."
            )
        elif cmd in ("/add", "/remove") and 0 < len(arg) <= 80:
            with db:
                db.execute(
                    "insert or ignore into interests values(?)"
                    if cmd == "/add"
                    else "delete from interests where name=?",
                    (arg,),
                )
            reply = "관심사를 저장했습니다."
        elif cmd == "/recent":
            reply = (
                "\n".join(
                    (r["ko"] or json.loads(r["data"])["title"])[:180]
                    for r in db.execute(
                        "select * from items where urgent and state='sent' order by sent desc limit 10"
                    )
                )
                or "발송 기록이 없습니다."
            )
        elif cmd == "/held" and not arg:
            reply = (
                "\n".join(
                    f"#{r['id']} " + json.loads(r["data"])["title"][:120]
                    for r in db.execute(
                        "select * from items where state='candidate' order by id desc limit 10"
                    )
                )
                or "보류 후보가 없습니다."
            )
        elif cmd == "/held" and arg.isdecimal() and len(arg) < 15:
            r = self.store.item(int(arg))
            if r and r["state"] == "candidate":
                await self.send(self.owner, r["id"], render(self.store, r))
                return
            reply = "해당 보류 후보가 없습니다."
        elif cmd == "/retry" and arg.isdecimal() and len(arg) < 15:
            with db:
                changed = db.execute(
                    "update items set state='ready',due=0 where id=? and state='uncertain'",
                    (int(arg),),
                ).rowcount
            reply = (
                "재발송을 예약했습니다." if changed else "재발송 대기 건이 없습니다."
            )
        else:
            return
        await self.call("sendMessage", chat_id=self.owner, text=reply[:4000])

    async def updates(self):
        db = self.store.db
        row = db.execute("select v from meta where k='telegram_offset'").fetchone()
        for u in await self.call(
            "getUpdates",
            offset=int(row[0]) if row else 0,
            timeout=20,
            allowed_updates=["message", "callback_query"],
        ):
            try:
                await self.update(u)
            except BotAPIError as exc:
                if exc.status != 400:
                    raise
            with db:
                db.execute(
                    "insert or replace into meta values('telegram_offset',?)",
                    (str(u["update_id"] + 1),),
                )

    async def send_step(self):
        # Bot API has no history lookup. Ambiguous requests require owner review.
        row = self.store.db.execute(
            "select * from items where state in ('ready','retry') and due<=? order by urgent desc,id limit 1",
            (int(time.time()),),
        ).fetchone()
        if row:
            await deliver(self.store, row, self)

    async def run(self):
        self.store.cfg["settings"].setdefault("collect_all", True)
        with self.store.db:
            self.store.db.execute(
                "update items set state='uncertain' where state='sending'"
            )
        if (await self.call("getWebhookInfo"))["url"]:
            raise SystemExit(
                "기존 웹훅을 확인하세요. 뉴스 봇에는 전용 봇 토큰이 필요합니다."
            )

        async def repeat(delay, fn, *args):
            while True:
                try:
                    await fn(*args)
                except Exception as exc:
                    logging.warning(
                        "%s: %s",
                        fn.__name__,
                        str(exc)
                        if isinstance(exc, BotAPIError)
                        else type(exc).__name__,
                    )
                await asyncio.sleep(delay)

        async def digest():
            self.store.digest(int(time.time()))

        await asyncio.gather(
            repeat(1, self.updates),
            repeat(1, self.send_step),
            repeat(1, translation_step, self.store),
            repeat(15, digest),
            *(
                repeat(self.store.cfg["settings"]["poll_seconds"], poll, self.store, s)
                for s in self.store.cfg["sources"]
                if s.get("enabled", True)
            ),
        )
