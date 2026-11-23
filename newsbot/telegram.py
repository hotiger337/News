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

from . import review
from .bot import LABELS, poll, translation_step
from .archive import archive_step
from .site_export import sync_step
from .core import KST, render
from .delivery import deliver
from .reports import activity, scorecard, source_report
from .sources import enabled, required


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
            exc = BotAPIError(result.get("error_code", r.status_code))
            exc.unchanged = (
                "message is not modified" in result.get("description", "").lower()
            )
            raise exc
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
        self.message_lock = asyncio.Lock()
        self.api = API(required("TELEGRAM_BOT_TOKEN"))
        self.owner = int(required("TELEGRAM_OWNER_ID"))
        self.breaking = int(os.getenv("TELEGRAM_BREAKING_CHAT_ID") or self.owner)
        self.interest = int(os.getenv("TELEGRAM_INTEREST_CHAT_ID") or self.breaking)

    def channel(self, row):
        return self.breaking if row["urgent"] else self.interest

    async def call(self, method, **kwargs):
        return await asyncio.to_thread(self.api.call, method, **kwargs)

    def article_payload(self, n, body):
        keys = [
            {"text": label, "callback_data": f"news:{n}:{act}"}
            for act, label in LABELS.items()
        ]
        return dict(
            text=body,
            reply_markup={"inline_keyboard": [keys[:2], keys[2:]]},
            link_preview_options={"is_disabled": True},
        )

    async def send(self, channel, n, body):
        msg = await self.call(
            "sendMessage", chat_id=channel, **self.article_payload(n, body)
        )
        return msg["message_id"]

    async def edit_step(self):
        async with self.message_lock:
            await self.edit_one()

    async def edit_one(self):
        db = self.store.db
        row = db.execute(
            """select i.*,e.version from source_edits e join items i on i.id=e.item
                          where i.state='sent' and e.due<=? order by e.item limit 1""",
            (int(time.time()),),
        ).fetchone()
        if not row:
            return
        n = row["id"]
        payload = self.article_payload(n, render(self.store, row, compact=True))
        if db.execute(
            "select 1 from hidden_ratings where chat=? and message=?",
            (row["channel"], row["message"]),
        ).fetchone():
            payload["reply_markup"] = {"inline_keyboard": []}
        try:
            await self.call(
                "editMessageText",
                chat_id=int(row["channel"]),
                message_id=int(row["message"]),
                **payload,
            )
        except BotAPIError as exc:
            if not getattr(exc, "unchanged", False):
                with db:
                    db.execute(
                        "update source_edits set due=? where item=?",
                        (int(time.time()) + 900, n),
                    )
                return
        with db:
            db.execute(
                "delete from source_edits where item=? and version=?",
                (n, row["version"]),
            )

    async def update(self, u):
        async with self.message_lock:
            await self.handle_update(u)

    async def handle_update(self, u):
        callback = u.get("callback_query")
        if callback:
            response = "등록 사용자 전용입니다."
            if callback["from"]["id"] == self.owner:
                data = callback.get("data", "")
                rm = re.fullmatch(
                    r"rv:(\d+):(\d+):([01]):(up|down|undo|prev|next|toggle):(\d+)", data
                )
                if rm:
                    slot, page, original = int(rm[1]), int(rm[2]), bool(int(rm[3]))
                    action, n = rm[4], int(rm[5])
                    db = self.store.db
                    if action in ("up", "down", "undo"):
                        if not db.execute(
                            "select 1 from review_items where slot=? and item=? and pos>=? and pos<?",
                            (
                                slot,
                                n,
                                page * review.PAGE_SIZE,
                                (page + 1) * review.PAGE_SIZE,
                            ),
                        ).fetchone():
                            return
                        self.store.vote(self.owner, n, action)
                    elif action == "toggle":
                        original = not original
                    else:
                        page += 1 if action == "next" else -1
                    msg = callback["message"]
                    await self.call(
                        "answerCallbackQuery", callback_query_id=callback["id"]
                    )
                    await self.call(
                        "editMessageText",
                        chat_id=msg["chat"]["id"],
                        message_id=msg["message_id"],
                        **review.payload(db, self.owner, slot, page, original),
                    )
                    return
                m = re.fullmatch(
                    r"news:(\d+):(up|down|batch|undo)", callback.get("data", "")
                )
                if m and self.store.item(int(m[1])):
                    self.store.vote(self.owner, int(m[1]), m[2])
                    msg = callback.get("message")
                    if msg:
                        chat, mid = msg["chat"]["id"], msg["message_id"]
                        with self.store.db:
                            self.store.db.execute(
                                "insert or replace into hidden_ratings values(?,?,?)",
                                (str(chat), str(mid), int(m[1])),
                            )
                        await self.call(
                            "editMessageReplyMarkup",
                            chat_id=chat,
                            message_id=mid,
                            reply_markup={"inline_keyboard": []},
                        )
                    response = (
                        "묶음 선호를 기록했습니다. 일반 기사는 12시와 20시에 발송합니다."
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
            reply = "/review 최근 뉴스 리뷰 · /status 상태 · /scores 평가 성적표 · /sources 출처 실적 · /rate 번호 평가 다시 열기 · /recent 최근 속보 · /interests 관심사 · /add 이름 · /remove 이름 · /held 보류 후보 · /held 번호 평가 · /retry 번호 응답 유실 건 재발송"
        elif cmd == "/review":
            if arg:
                if not arg.isdecimal() or len(arg) > 12:
                    reply = "/review 또는 /status에 표시된 /review 번호를 입력하세요."
                    await self.call("sendMessage", chat_id=self.owner, text=reply)
                    return
                latest = db.execute(
                    "select slot from reviews where slot=?", (int(arg),)
                ).fetchone()
            else:
                latest = db.execute(
                    "select slot from reviews order by slot desc limit 1"
                ).fetchone()
            if latest:
                await self.call(
                    "sendMessage",
                    chat_id=self.owner,
                    **review.payload(db, self.owner, latest[0], 0),
                )
                review.confirmed(db, latest[0], int(time.time()))
                return
            reply = (
                "해당 리뷰가 없습니다."
                if arg
                else "첫 리뷰를 준비하고 있습니다. 한국시간 12시와 20시에 일반 기사를 모아 보냅니다."
            )
        elif cmd == "/scores":
            reply = scorecard(db, self.owner)
        elif cmd == "/sources":
            reply = source_report(db, self.owner, int(time.time()))
        elif cmd == "/rate" and arg.isdecimal() and len(arg) < 15:
            n = int(arg)
            row = db.execute(
                "select * from hidden_ratings where item=? order by rowid desc limit 1",
                (n,),
            ).fetchone()
            if row:
                await self.call(
                    "editMessageReplyMarkup",
                    chat_id=int(row["chat"]),
                    message_id=int(row["message"]),
                    reply_markup=self.article_payload(n, "")["reply_markup"],
                )
                with db:
                    db.execute(
                        "delete from hidden_ratings where chat=? and message=?",
                        (row["chat"], row["message"]),
                    )
                reply = "평가 버튼을 다시 열었습니다."
            else:
                reply = "닫힌 평가가 없습니다."
        elif cmd == "/status":
            lines = ["속보 즉시 · 일반 기사 리뷰 12:00 / 20:00 KST"]
            lines += activity(db, int(time.time()))
            waiting = db.execute(
                "select count(*) from items where not urgent and state in ('ready','review_queued','retry')"
            ).fetchone()[0]
            lines.append(f"리뷰 대기 {waiting}건")
            for (slot,) in db.execute(
                "select slot from reviews where state='uncertain' order by slot desc limit 5"
            ):
                stamp = datetime.fromtimestamp(slot, KST).strftime("%m-%d %H:%M")
                lines.append(
                    f"{stamp} 리뷰 발송 응답 확인 필요 · 다시 열기 /review {slot}"
                )
            if not os.getenv("TRANSLATOR_KEY", "").strip():
                lines.append("해외 기사 번역 · 서버 내 영어→한국어 모델")
            for src in self.store.cfg["sources"]:
                r = db.execute(
                    "select * from sources where id=?", (src["id"],)
                ).fetchone()
                if not enabled(src):
                    lines.append(
                        f"{src['id']} · 꺼짐 · {src.get('verification', '설정 필요')}"
                    )
                    continue
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
                if json.loads(r["data"])["lang"] != "ko" and not r["ko"]:
                    with db:
                        db.execute(
                            "update items set state='translate',due=0 where id=?",
                            (r["id"],),
                        )
                    await self.call(
                        "sendMessage",
                        chat_id=self.owner,
                        text="한국어 번역 후 발송합니다.",
                    )
                    return
                await self.send(
                    self.owner, r["id"], render(self.store, r, compact=True)
                )
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

    async def review_step(self):
        now = int(time.time())
        db = self.store.db
        review.prepare(db, now)
        r = db.execute(
            "select slot from reviews where state='pending' and due<=? order by slot limit 1",
            (now,),
        ).fetchone()
        if not r:
            return
        slot = r[0]
        body = review.payload(db, self.owner, slot, 0)
        with db:
            db.execute("update reviews set state='sending' where slot=?", (slot,))
        try:
            await self.call("sendMessage", chat_id=self.interest, **body)
        except BotAPIError as exc:
            state = (
                "pending" if exc.status in (400, 401, 403, 404, 429) else "uncertain"
            )
            with db:
                db.execute(
                    "update reviews set state=?,due=? where slot=?",
                    (state, now + 120, slot),
                )
            return
        review.confirmed(db, slot, int(time.time()))

    async def send_step(self):
        # Bot API has no history lookup. Ambiguous requests require owner review.
        row = self.store.db.execute(
            "select * from items where urgent and state in ('ready','retry') and due<=? order by id limit 1",
            (int(time.time()),),
        ).fetchone()
        if row and not row["ko"] and json.loads(row["data"])["lang"] != "ko":
            with self.store.db:
                self.store.db.execute(
                    "update items set state='translate',attempt=0,due=0 where id=?",
                    (row["id"],),
                )
            return
        if row:
            await deliver(self.store, row, self, compact=True)

    async def run(self):
        self.store.cfg["settings"]["collect_all"] = True
        self.store.db.executescript(review.SCHEMA)
        with self.store.db:
            self.store.db.execute(
                "update reviews set state='uncertain' where state='sending'"
            )
            self.store.db.execute("""update items set state='translate',due=0 where not urgent and state in ('ready','retry')
                                     and ko is null and json_extract(data,'$.lang')<>'ko'""")
            if not os.getenv("TRANSLATOR_KEY", "").strip():
                first = self.store.db.execute(
                    "insert or ignore into meta values('opus_tc_big_v1','1')"
                ).rowcount
                if first:
                    self.store.db.execute("""update items set state='translate',due=0,attempt=0,
                        error=null,ko=null,translated=null where state in ('ready','retry','translate')
                        and sent is null and json_extract(data,'$.lang')='en'""")
        self.store.recover_representatives(int(time.time()))
        review.prepare(self.store.db, int(time.time()))
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

        await asyncio.gather(
            repeat(1, self.updates),
            repeat(1, self.send_step),
            repeat(2, self.edit_step),
            repeat(1, translation_step, self.store),
            repeat(10, self.review_step),
            repeat(300, archive_step, self.store),
            repeat(30, sync_step, self.store, self.breaking),
            *(
                repeat(self.store.cfg["settings"]["poll_seconds"], poll, self.store, s)
                for s in self.store.cfg["sources"]
                if enabled(s)
            ),
        )
