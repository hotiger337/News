"""Twice-daily discovery reports for the existing Discord interest channel."""

import asyncio
import json
import random
from datetime import datetime

import discord

from .verification import collect, safe_page
from .core import KST


# Defaults also support an existing server-side configuration from the API version.
DEFAULT_DOMAINS = ["bbc.com", "bbc.co.uk", "news.sky.com", "npr.org", "yna.co.kr",
                   "sbs.co.kr", "abc.net.au", "dw.com", "nvidianews.nvidia.com", "fullfact.org"]
DEFAULT_CHANNELS = ["UCLA_DiR1FfKNvjuUpBHmylQ", "UCsXVk37bltHxD1rDPwtNM8Q",
                    "UC_x5XG1OV2P6uZZ5FSM9Ttw", "UCi7Zk9baY1tvdlgxIML8MXg"]


def recent_pages(store, domains, now):
    rss = {s["id"] for s in store.cfg["sources"] if s["adapter"] == "rss"}
    pages = {}
    counts = {}
    for row in store.db.execute(
        "select source,data from items where found>=? order by found desc,id desc",
        (now - 3 * 86400,),
    ):
        source = row["source"]
        if source not in rss or counts.get(source, 0) >= 8:
            continue
        item = json.loads(row["data"])
        if not now - 3 * 86400 <= item["published"] <= now:
            continue
        for url in item["links"]:
            if url not in pages and safe_page(url, domains):
                pages[url] = item["title"]
                counts[source] = counts.get(source, 0) + 1
                break
        if len(pages) >= 64:
            break
    return list(pages.items())


def select(entries, interests, cfg, seen, rng, limit=2):
    registered = {term.casefold() for term in interests}
    aliases = registered.copy()
    for group in ("topics", "companies"):
        for name, terms in cfg.get(group, {}).items():
            names = {term.casefold() for term in [name, *terms]}
            if names & registered:
                aliases.update(names)
    related, broad = {}, {}
    for platform, url, title, source, evidence in entries:
        if url in seen:
            continue
        matched = any(term in (title + " " + source).casefold() for term in aliases)
        item = (platform, url, title, evidence)
        if matched:
            related.setdefault(url, item)
        else:
            broad.setdefault(url, item)
    outside = [item for url, item in broad.items() if url not in related]
    # Give each available platform a place in the related section, keeping source rank.
    ranked = list(related.values())
    firsts = {}
    for item in ranked:
        firsts.setdefault(item[0], item)
    ordered = list(firsts.values()) + [item for item in ranked if item not in firsts.values()]
    return rng.sample(outside, min(limit, len(outside))), ordered[:limit]


def message(slot, groups):
    lines = ["**유튜브 · 레딧 브리핑**", slot + " KST"]
    for label, items in zip(("관심사 밖 랜덤", "관심사 관련"), groups):
        lines.extend(["", "**" + label + "**"])
        if not items:
            lines.append("새 후보 없음")
        for platform, url, title, evidence in items:
            safe = discord.utils.escape_markdown(" ".join(title.split()))[:130]
            lines.append(f"• {platform} · {safe}\n원문 <{url}> · 확인 <{evidence}>")
    return "\n".join(lines)


async def report(bot):
    store = bot.store
    now = datetime.now(KST)
    if now.hour not in store.cfg["social"]["hours_kst"]:
        return
    slot = now.replace(minute=0, second=0, microsecond=0)
    label = slot.strftime("%Y-%m-%d %H:%M")
    db = store.db
    db.execute("""create table if not exists social_reports
                  (slot text primary key, body text, urls text, attempted integer, sent integer)""")
    row = db.execute("select * from social_reports where slot=?", (label,)).fetchone()
    if row and row["sent"]:
        return
    if not row:
        interests = [r[0] for r in db.execute("select name from interests order by name")]
        cfg = store.cfg["social"]
        domains = cfg.get("evidence_domains", DEFAULT_DOMAINS)
        channels = cfg.get("youtube_channels", DEFAULT_CHANNELS)
        pages = recent_pages(store, domains, int(now.timestamp()))
        feeds = cfg.get("fact_check_feeds", ["https://fullfact.org/feed/"])
        entries = await asyncio.to_thread(collect, pages, channels, domains, feeds)
        seen = {url for r in db.execute("select urls from social_reports")
                for url in json.loads(r[0])}
        groups = select(entries, interests, store.cfg, seen, random)
        urls = [item[1] for group in groups for item in group]
        if not urls:
            with db:
                db.execute("insert into social_reports values(?, ?, ?, 0, 1)", (label, "", "[]"))
            return
        with db:
            db.execute("insert into social_reports values(?,?,?,0,0)",
                       (label, message(label, groups), json.dumps(urls)))
        row = db.execute("select * from social_reports where slot=?", (label,)).fetchone()

    channel = await bot.transport.target(bot.interest_id)
    delivered = False
    if row["attempted"]:
        async for msg in channel.history(after=slot, limit=None):
            if msg.author.id == bot.user.id and msg.content == row["body"]:
                delivered = True
                break
    if not delivered:
        with db:
            db.execute("update social_reports set attempted=1 where slot=?", (label,))
        await channel.send(row["body"], suppress_embeds=True)
    with db:
        db.execute("update social_reports set sent=1 where slot=?", (label,))
