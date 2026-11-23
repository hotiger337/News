"""Mirror confirmed Telegram deliveries of public RSS articles to the owner's Site."""

import asyncio
import json
import os
import re
import time
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlsplit

import requests

from .core import url_key


ENDPOINT = "https://issue-index-brief.springcaramel1.chatgpt.site/api/telegram-import"
CURSOR = "site_telegram_export_v1"
PUBLIC_DOMAINS = """sbs.co.kr bbc.co.uk bbci.co.uk bbc.com skynews.com sky.com sec.gov
arxiv.org nvidia.com coreweave.com amd.com federalreserve.gov yna.co.kr khan.co.kr etnews.com
npr.org globalnews.ca mexiconewsdaily.com buenosairesherald.com dw.com lemonde.fr elpais.com
japantimes.co.jp thehindu.com dawn.com adaderana.lk channelnewsasia.com philstar.com
abc.net.au rnz.co.nz aljazeera.com dailynewsegypt.com premiumtimesng.com capitalfm.co.ke
myjoyonline.com""".split()


def public_domain(url):
    if not isinstance(url, str) or len(url) > 2048 or re.search(r"[\s<>\\]", url):
        return None
    try:
        parts = urlsplit(url)
        if (parts.scheme not in ("http", "https") or parts.username or parts.password
                or parts.port is not None or not parts.path.strip("/")):
            return None
        if any(re.search(r"token|secret|password|signature|session|credential|api.?key|^x-amz-",
                         name, re.I) for name, _ in parse_qsl(parts.query)):
            return None
        host = parts.hostname or ""
        return next((domain for domain in PUBLIC_DOMAINS if host == domain or host.endswith("." + domain)), None)
    except ValueError:
        return None


def public_sources(cfg):
    return {s["id"]: (s["publisher"], public_domain(s["url"])) for s in cfg.get("sources", [])
            if s.get("adapter") == "rss" and public_domain(s.get("url"))}


def public_article(row, sources):
    source = sources.get(row["source"])
    if not source:
        return None
    publisher, domain = source
    domains = {domain}
    if domain in {"bbci.co.uk", "bbc.com", "bbc.co.uk"}:
        domains.update(("bbc.co.uk", "bbc.com"))
    if domain == "skynews.com":
        domains.add("sky.com")
    item = json.loads(row["data"])
    url = next((u for u in item.get("links", []) if public_domain(u) in domains), None)
    if not url:
        return None
    return {"source": publisher, "title": row["ko"] or item["title"],
            "originalTitle": item["title"], "url": url_key(url),
            "date": datetime.fromtimestamp(item["published"], timezone.utc).isoformat(),
            "summary": (item.get("detail") or "")[:230],
            "deliveredAt": datetime.fromtimestamp(row["sent"], timezone.utc).isoformat(),
            "breaking": bool(row["urgent"])}


def deliveries(db, channel, since, after_id, until, *, recent=False):
    reviewed = "0"
    if db.execute("select count(*) from sqlite_master where type='table' and name in ('reviews','review_items')").fetchone()[0] == 2:
        reviewed = """(i.state='reviewed' and exists (
            select 1 from review_items x join reviews r on r.slot=x.slot
            where x.item=i.id and r.state='sent'))"""
    order = "i.sent desc,i.id desc" if recent else "i.sent,i.id"
    limit = 1000 if recent else 50
    return db.execute(f"""
        select i.* from items i where (i.sent,i.id)>(?,?) and i.sent<=?
        and ((i.urgent=1 and i.state='sent' and i.channel=? and i.message is not null)
        or {reviewed}) order by {order} limit ?""", (since, after_id, until, str(channel), limit)).fetchall()


def push(rows, token):
    response = requests.post(
        ENDPOINT, json={"articles": rows},
        headers={"OAI-Sites-Authorization": "Bearer " + token},
        timeout=(5, 30), allow_redirects=False,
    )
    if not 200 <= response.status_code < 300 or response.json().get("accepted") != len(rows):
        raise ValueError("Site가 전송 묶음을 확인하지 않았습니다.")


async def refresh_site():
    """Prompt due RSS sources while the private site has no active readers."""
    token = os.getenv("NEWS_SITE_TOKEN", "").strip()
    if not token:
        return

    def refresh():
        with requests.get(ENDPOINT.rsplit("/", 1)[0] + "/live",
                          headers={"OAI-Sites-Authorization": "Bearer " + token},
                          timeout=(5, 65), allow_redirects=False, stream=True) as response:
            if response.status_code != 200:
                raise ValueError("사이트 RSS 갱신 실패")

    await asyncio.to_thread(refresh)


async def sync_step(store, channel):
    token = os.getenv("NEWS_SITE_TOKEN", "").strip()
    if not token:
        return
    db = store.db
    now = int(time.time())
    with db:
        db.execute("create index if not exists site_delivery_time on items(sent,id)")
        db.execute("insert or ignore into meta values(?,?)", (CURSOR, json.dumps([now - 7 * 86400, 0])))
    since, last = json.loads(db.execute("select v from meta where k=?", (CURSOR,)).fetchone()[0])
    sources = public_sources(store.cfg)
    # Allow the current clock second to close before stepping past any IDs in it.
    rows = deliveries(db, channel, since, last, now - 1)
    if not rows:
        return
    payload = [article for row in rows if (article := public_article(row, sources))]
    if payload:
        await asyncio.to_thread(push, payload, token)
    with db:
        db.execute("update meta set v=? where k=?", (json.dumps([rows[-1]["sent"], rows[-1]["id"]]), CURSOR))
