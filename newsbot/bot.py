import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone

import discord
from discord import app_commands

from .core import KST
from .delivery import deliver
from .sources import ADAPTERS, error_label, required, translate

LABELS = {
    "up": "👍 더 보기",
    "down": "👎 덜 보기",
    "batch": "🕓 묶어서 받기",
    "undo": "평가 취소",
}


class Rating(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"news:(?P<n>[0-9]+):(?P<act>up|down|batch|undo)",
):
    def __init__(self, n, action):
        self.n = n
        self.action = action
        super().__init__(
            discord.ui.Button(label=LABELS[action], custom_id=f"news:{n}:{action}")
        )

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["n"]), match["act"])

    async def callback(self, interaction):
        bot = interaction.client
        if not bot.allowed(interaction):
            await interaction.response.send_message(
                "등록 사용자 전용입니다.", ephemeral=True
            )
            return
        bot.store.vote(interaction.user.id, self.n, self.action)
        await interaction.response.send_message(
            "관련 관심 자료의 전달 시간을 묶음으로 저장했습니다. 필수 속보는 계속 즉시 발송합니다."
            if self.action == "batch"
            else "평가를 취소했습니다."
            if self.action == "undo"
            else "내용 평가를 저장했습니다.",
            ephemeral=True,
        )


def buttons(n):
    v = discord.ui.View(timeout=None)
    for act in LABELS:
        v.add_item(Rating(n, act))
    return v


class DiscordTransport:
    def __init__(self, bot):
        self.bot = bot

    def channel(self, row):
        return self.bot.break_id if row["urgent"] else self.bot.interest_id

    async def target(self, id_):
        ch = self.bot.get_channel(int(id_)) or await self.bot.fetch_channel(int(id_))
        if getattr(ch, "guild", None) is None or ch.guild.id != self.bot.guild_id:
            raise ValueError("unexpected channel guild")
        return ch

    async def send(self, channel, n, body):
        ch = await self.target(channel)
        msg = await ch.send(body, view=buttons(n), suppress_embeds=True)
        return msg.id

    async def find(self, row):
        if not row["started"]:
            return None
        ch = await self.target(row["channel"])
        since = datetime.fromtimestamp(row["started"] - 5, timezone.utc)
        tag = f"news:{row['id']}:up"
        async for msg in ch.history(after=since, limit=None):
            if msg.author.id == self.bot.user.id and any(
                getattr(b, "custom_id", None) == tag
                for line in msg.components
                for b in getattr(line, "children", [])
            ):
                return msg.id, int(msg.created_at.timestamp())
        return None


async def poll(store, s):
    db = store.db
    row = db.execute("select cursor from sources where id=?", (s["id"],)).fetchone()
    old = json.loads(row[0]) if row and row[0] else None
    try:
        entries, cursor, gap = await asyncio.to_thread(ADAPTERS[s["adapter"]], s, old)
        store.ingest(s, entries, cursor, gap)
    except Exception as exc:
        error = error_label(exc)
        with db:
            db.execute(
                "insert or replace into source_errors values(?,?,?)",
                (s["id"], int(time.time()), error),
            )
            db.execute(
                """insert into sources(id,error,gap) values(?,?,?) on conflict(id) do update set
                          error=excluded.error,gap=coalesce(excluded.gap,sources.gap)""",
                (
                    s["id"],
                    error,
                    "X 복구 위치 확인 필요" if old and s["adapter"] == "fx" else None,
                ),
            )


async def translation_step(store):
    now = int(time.time())
    row = store.db.execute(
        "select * from items where state='translate' and due<=? order by id limit 1",
        (now,),
    ).fetchone()
    if not row:
        return
    try:
        data = json.loads(row["data"])
        names = {
            n
            for company, aliases in store.cfg["companies"].items()
            for n in [company, *aliases]
        }
        names.update(store.cfg.get("translation", {}).get("names", []))
        ko = await asyncio.to_thread(translate, data["title"], data["lang"], names)
        with store.db:
            store.db.execute(
                """update items set ko=?,translated=?,state='ready',due=0,attempt=0,error=null
                               where id=?""",
                (ko, int(time.time()), row["id"]),
            )
    except Exception as exc:
        with store.db:
            store.db.execute(
                "update items set error=?,due=?,attempt=attempt+1 where id=?",
                (
                    error_label(exc),
                    now + min(1800, 30 * 2 ** min(row["attempt"], 6)),
                    row["id"],
                ),
            )


class NewsBot(discord.Client):
    def __init__(self, store):
        super().__init__(
            intents=discord.Intents(guilds=True),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        self.store = store
        self.guild_id = int(required("DISCORD_GUILD_ID"))
        self.owner = int(required("DISCORD_OWNER_ID"))
        self.break_id = int(required("BREAKING_CHANNEL_ID"))
        self.interest_id = int(os.getenv("INTEREST_CHANNEL_ID") or self.break_id)
        self.tree = app_commands.CommandTree(self)
        self.workers = []
        self.transport = DiscordTransport(self)
        self.commands()

    def allowed(self, i):
        return i.guild_id == self.guild_id and i.user.id == self.owner

    def commands(self):
        guild = discord.Object(id=self.guild_id)
        db = self.store.db

        async def check(i):
            if self.allowed(i):
                return True
            await i.response.send_message("등록 사용자 전용입니다.", ephemeral=True)
            return False

        def command(name, desc):
            return self.tree.command(name=name, description=desc, guild=guild)

        @command("상태", "출처와 발송 상태")
        async def status(i: discord.Interaction):
            if not await check(i):
                return

            def fmt(t):
                return (
                    datetime.fromtimestamp(t, KST).strftime("%m-%d %H:%M")
                    if t
                    else "없음"
                )

            lines = []
            for s in self.store.cfg["sources"]:
                r = db.execute(
                    "select * from sources where id=?", (s["id"],)
                ).fetchone()
                last = db.execute(
                    "select at,label from source_errors where source=?", (s["id"],)
                ).fetchone()
                lines.append(
                    f"{s['id']} · "
                    + ("활성" if s.get("enabled", True) else "꺼짐")
                    + " · 최근 성공 "
                    + fmt(r["success"] if r else None)
                    + " · "
                    + (
                        (r["error"] or "정상")
                        if r
                        else s.get("verification", "검증 대기")
                    )
                    + (
                        " · 최근 오류 " + fmt(last["at"]) + " " + last["label"]
                        if last
                        else ""
                    )
                    + (" · " + r["gap"] if r and r["gap"] else "")
                )
            for state in ("translate", "ready", "retry", "sending", "uncertain"):
                count, err = db.execute(
                    "select count(*),max(error) from items where state=?", (state,)
                ).fetchone()
                lines.append(f"{state} {count} " + (err or ""))
            for r in db.execute(
                "select * from items where sent is not null order by sent desc limit 3"
            ):
                ts = json.loads(r["data"])["published"]
                lines.append(
                    f"#{r['id']} 게시→발송 {r['sent'] - ts}초 · 발견→발송 {r['sent'] - r['found']}초"
                )
            await i.response.send_message("\n".join(lines)[:1950], ephemeral=True)

        @command("관심사", "등록된 관심사 확인")
        async def interests(i: discord.Interaction):
            if await check(i):
                await i.response.send_message(
                    ", ".join(x[0] for x in db.execute("select name from interests"))[
                        :1900
                    ]
                    or "등록된 관심사가 없습니다.",
                    ephemeral=True,
                )

        async def edit(i, name, add):
            if not await check(i):
                return
            name = name.strip()
            if not 1 <= len(name) <= 80:
                await i.response.send_message("1~80자로 입력하세요.", ephemeral=True)
                return
            with db:
                db.execute(
                    "insert or ignore into interests values(?)"
                    if add
                    else "delete from interests where name=?",
                    (name,),
                )
            await i.response.send_message("관심사를 저장했습니다.", ephemeral=True)

        @command("관심추가", "관심사 추가")
        async def add(i: discord.Interaction, 이름: str):
            await edit(i, 이름, True)

        @command("관심삭제", "관심사 삭제")
        async def remove(i: discord.Interaction, 이름: str):
            await edit(i, 이름, False)

        @command("최근속보", "최근 발송한 속보")
        async def recent(i: discord.Interaction):
            if not await check(i):
                return
            lines = [
                (r["ko"] or json.loads(r["data"])["title"])[:130]
                + f" https://discord.com/channels/{self.guild_id}/{r['channel']}/{r['message']}"
                for r in db.execute(
                    "select * from items where urgent and state='sent' order by sent desc limit 8"
                )
            ]
            await i.response.send_message(
                "\n".join(lines) or "발송 기록이 없습니다.", ephemeral=True
            )

        @command(
            "후보",
            "보류한 관심 자료를 평가합니다. 번호를 생략하면 최근 목록을 표시합니다.",
        )
        async def review(i: discord.Interaction, 번호: int = 0):
            if not await check(i):
                return
            row = self.store.item(번호)
            if row and row["state"] == "candidate":
                from .core import render

                await i.response.send_message(
                    render(self.store, row), view=buttons(번호), ephemeral=True
                )
            else:
                titles = [
                    f"#{r['id']} " + json.loads(r["data"])["title"][:120]
                    for r in db.execute(
                        "select * from items where state='candidate' order by id desc limit 10"
                    )
                ]
                await i.response.send_message(
                    "\n".join(titles) or "보류 후보가 없습니다.", ephemeral=True
                )

    async def setup_hook(self):
        self.add_dynamic_items(Rating)
        await self.tree.sync(guild=discord.Object(id=self.guild_id))

        async def repeat(fn, delay, *args):
            await self.wait_until_ready()
            while not self.is_closed():
                try:
                    await fn(*args)
                except Exception as exc:
                    logging.warning("작업 %s 오류 %s", fn.__name__, error_label(exc))
                await asyncio.sleep(delay)

        def start(fn, delay, *args):
            self.workers.append(asyncio.create_task(repeat(fn, delay, *args)))

        for s in self.store.cfg["sources"]:
            if s.get("enabled", True):
                start(poll, self.store.cfg["settings"]["poll_seconds"], self.store, s)
        start(translation_step, 1, self.store)
        start(self.send_step, 1)
        start(self.digest_step, 15)

    async def send_step(self):
        row = self.store.db.execute(
            """select * from items where state in ('ready','retry','uncertain','sending')
                     and due<=? order by urgent desc,id limit 1""",
            (int(time.time()),),
        ).fetchone()
        if row:
            await deliver(self.store, row, self.transport)

    async def digest_step(self):
        self.store.digest(int(time.time()))

    async def close(self):
        for task in self.workers:
            task.cancel()
        await asyncio.gather(*self.workers, return_exceptions=True)
        await super().close()
