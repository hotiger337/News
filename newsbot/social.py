"""Twice-daily discovery reports for the existing Discord interest channel."""

import asyncio
import html
import json
import logging
import os
import random
import re
from datetime import datetime, timedelta, timezone

import discord
import requests

from .verification import verify
from .core import KST
from .sources import error_label, required


def youtube(queries, regions, now):
    key = required("YOUTUBE_API_KEY")
    candidates = []

    def fetch(endpoint, **params):
        response = requests.get(
            "https://www.googleapis.com/youtube/v3/" + endpoint,
            params={"key": key, "part": "snippet", "maxResults": 50, **params},
            timeout=15,
        )
        response.raise_for_status()
        return response.json()["items"]

    for region in regions:
        for item in fetch("videos", chart="mostPopular", regionCode=region):
            candidates.append((item["id"], item["snippet"], False))
    since = (now - timedelta(days=3)).astimezone(timezone.utc).isoformat()
    if queries:
        for item in fetch("search", type="video", order="viewCount",
                          publishedAfter=since, q="|".join(queries)):
            candidates.append((item["id"]["videoId"], item["snippet"], True))
    return [("YouTube", "https://www.youtube.com/watch?v=" + uid,
             html.unescape(info["title"]), info.get("channelTitle", ""), related)
            for uid, info, related in candidates if re.fullmatch(r"[\w-]{11}", uid)]


def reddit(queries):
    headers = {"User-Agent": required("REDDIT_USER_AGENT")}
    token = requests.post(
        "https://www.reddit.com/api/v1/access_token",
        auth=(required("REDDIT_CLIENT_ID"), required("REDDIT_CLIENT_SECRET")),
        headers=headers, data={"grant_type": "client_credentials"}, timeout=15,
    )
    token.raise_for_status()
    headers["Authorization"] = "Bearer " + token.json()["access_token"]
    feeds = [("r/all/rising", {}, False)]
    if queries:
        feeds.append(("search", {"q": " OR ".join(queries), "sort": "hot",
                                 "t": "week"}, True))
    results = []
    for endpoint, params, related in feeds:
        response = requests.get("https://oauth.reddit.com/" + endpoint,
                                headers=headers, params={"limit": 75, **params}, timeout=15)
        response.raise_for_status()
        for entry in response.json()["data"]["children"]:
            post = entry["data"]
            if post.get("over_18") or post.get("stickied") or not re.fullmatch(r"[a-z0-9]+", post["id"]):
                continue
            results.append(("Reddit", "https://www.reddit.com/comments/" + post["id"],
                            html.unescape(post["title"]), post["subreddit"], related))
    return results


def select(entries, interests, cfg, seen, rng, limit=2):
    registered = {term.casefold() for term in interests}
    aliases = registered.copy()
    for group in ("topics", "companies"):
        for name, terms in cfg.get(group, {}).items():
            names = {term.casefold() for term in [name, *terms]}
            if names & registered:
                aliases.update(names)
    related, broad = {}, {}
    for platform, url, title, source, targeted in entries:
        if url in seen:
            continue
        matched = any(term in (title + " " + source).casefold() for term in aliases)
        item = (platform, url, title)
        if targeted or matched:
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
        queries = random.sample(interests, min(4, len(interests)))
        results = await asyncio.gather(
            asyncio.to_thread(youtube, queries, store.cfg["social"]["regions"], now),
            asyncio.to_thread(reddit, queries), return_exceptions=True,
        )
        entries = []
        for platform, result in zip(("YouTube", "Reddit"), results):
            if isinstance(result, Exception):
                logging.warning("소셜 브리핑 %s: %s", platform, error_label(result))
            else:
                entries.extend(result)
        if not entries:
            return
        seen = {url for r in db.execute("select urls from social_reports")
                for url in json.loads(r[0])}
        shortlist = select(entries, interests, store.cfg, seen, random, limit=5)
        groups = await asyncio.to_thread(verify, shortlist, store.cfg["social"])
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
