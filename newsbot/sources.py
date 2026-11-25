import calendar
import email
import imaplib
import os
import re
import time
from datetime import datetime, timedelta
from email import policy
from email.utils import parseaddr, parsedate_to_datetime
from html import escape
from html.parser import HTMLParser
from urllib.parse import quote, urlsplit

import feedparser
import requests

from .core import KST, Item, breaking


def enabled(source):
    key = source.get("requires")
    return source.get("enabled", True) and (not key or bool(os.getenv(key, "").strip()))


def required(key):
    if not os.getenv(key, "").strip():
        raise ValueError("missing " + key)
    return os.environ[key].strip()


def urls(values):
    return list(
        dict.fromkeys(
            u
            for u in values
            if u
            and urlsplit(u).scheme in ("https", "http")
            and urlsplit(u).hostname
            and not any(c.isspace() or c in "<>" for c in u)
        )
    )


def overlap(old, ids):
    return (
        "이전 수집 창과 겹치는 ID 없음 · 일부 누락 가능"
        if old and old.get("ids") and ids and not set(ids).intersection(old["ids"])
        else ""
    )


class VisibleText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, text):
        self.parts.append(text)


def revision_detail(html):
    parser = VisibleText()
    parser.feed(html)
    text = " ".join(" ".join(parser.parts).split())
    sentences = re.split(r"(?<=[.!?])\s+", text)
    return " ".join(
        s
        for s in sentences
        if re.search(
            r"정정|철회|확정|검토|공식 확인|correct(?:ion|ed)|retract(?:ion|ed)|confirm(?:s|ed)?|"
            r"[$€£₩]\s*\d|\d[.,\d]*\s*(?:조|억|만|원|달러|million|billion|percent|%)",
            s,
            re.I,
        )
    )[:600]


def rss(s, old):
    headers = (
        {"User-Agent": required("SEC_USER_AGENT")}
        if "sec.gov" in urlsplit(s["url"]).netloc
        else {}
    )
    r = requests.get(s["url"], headers=headers, timeout=20)
    r.raise_for_status()
    feed = feedparser.parse(r.content)
    if not feed.version or (feed.bozo and not feed.entries):
        raise ValueError("invalid feed")
    entries = []
    for e in feed.entries:
        stamp = e.get("published_parsed") or e.get("updated_parsed")
        if not stamp or not e.get("title") or not (e.get("id") or e.get("link")):
            raise ValueError("incomplete RSS entry")
        entries.append(
            Item(
                e.get("id") or e.link,
                e.title,
                calendar.timegm(stamp),
                urls([e.get("link")]),
                categories=[t.term for t in e.get("tags", []) if "term" in t],
                detail=revision_detail(e.get("summary", "")),
                origin=e.get("source", {}).get("title", ""),
                evidence=next(
                    (
                        v.href
                        for v in e.get("links", [])
                        if v.get("rel") == "via" and urls([v.get("href")])
                    ),
                    "",
                ),
            )
        )
    ids = [i.uid for i in entries]
    return entries, {"ids": ids or (old or {}).get("ids", [])}, overlap(old, ids)


def bluesky_item(s, entry):
    post = entry["post"]
    record = post["record"]
    if entry.get("reason") or record.get("reply") or post["author"]["did"] != s["did"]:
        return None
    if post["author"]["handle"].casefold() != s["handle"].casefold():
        raise ValueError("publisher handle changed")
    uri = post["uri"]
    prefix = f"at://{s['did']}/app.bsky.feed.post/"
    key = uri.removeprefix(prefix)
    if not uri.startswith(prefix) or not re.fullmatch(r"[\w.~-]+", key):
        raise ValueError("invalid publisher post URI")
    stamp = datetime.fromisoformat(record["createdAt"])
    if stamp.tzinfo is None:
        raise ValueError("missing Bluesky timezone")
    embed = record.get("embed", {})
    media = embed.get("media", embed)
    links = [media.get("external", {}).get("uri")]
    raw = record["text"].encode("utf-8")
    text = record["text"]
    for facet in record.get("facets", []):
        targets = [
            f["uri"]
            for f in facet["features"]
            if f["$type"] == "app.bsky.richtext.facet#link"
        ]
        links.extend(targets)
        # A trailing displayed URL can be truncated. Keep its full target in the footer.
        index = facet["index"]
        if targets and index["byteEnd"] == len(raw):
            label = raw[index["byteStart"] :].decode()
            if re.fullmatch(r"(?:https?://|www\.)\S+", label):
                text = raw[: index["byteStart"]].decode()
    return Item(
        uri,
        text.strip(),
        int(stamp.timestamp()),
        urls(links),
        post=f"https://bsky.app/profile/{s['did']}/post/{key}",
    )


def bluesky(s, old):
    """Read original posts from the pinned AppView author and checkpoint even quiet polls."""
    params = {
        "actor": s["did"],
        "filter": "posts_no_replies",
        "includePins": "false",
        "limit": 100,
    }
    previous = set((old or {}).get("ids", []))
    head = []
    alerts = {}
    cursors = set()
    latest = (old or {}).get("latest", 0)
    matched = False
    gap = ""
    for _ in range(20):
        response = requests.get(
            "https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed",
            params=params,
            timeout=20,
        )
        response.raise_for_status()
        page = response.json()
        if not isinstance(page["feed"], list):
            raise ValueError("invalid author feed")
        for entry in page["feed"]:
            item = bluesky_item(s, entry)
            if not item:
                continue
            if len(head) < 100 and item.uid not in head:
                head.append(item.uid)
            latest = max(latest, item.published)
            known = item.uid in previous
            if (not matched or known) and breaking(s, item):
                alerts[item.uid] = item
            matched |= known
        if not previous or matched:
            break
        cursor = page.get("cursor")
        if not cursor or cursor in cursors:
            gap = "이전 Bluesky 게시물과 겹치지 않음 · 일부 누락 가능"
            break
        cursors.add(cursor)
        params["cursor"] = cursor
    else:
        gap = "Bluesky 20페이지 복구 한도 · 일부 누락 가능"

    entries = list(alerts.values())
    if gap:
        # All former anchors may have been deleted. Do not replay the publisher's history.
        entries = [item for item in entries if item.published >= old["latest"]]
    checkpoint = {"ids": head or (old or {}).get("ids", []), "latest": latest}
    return entries, checkpoint, gap


def xitem(s, x):
    if x.get("type") != "status":
        raise ValueError("unknown timeline entry")
    if (
        x.get("reposted_by")
        or x["author"]["screen_name"].lower() != s["handle"].lower()
    ):
        return None
    facets = x.get("raw_text", {}).get("facets", [])
    links = urls(
        [(x.get("card") or {}).get("url")]
        + [f.get("replacement") for f in facets if f.get("type") == "url"]
    )
    links = [
        u
        for u in links
        if urlsplit(u).hostname not in ("x.com", "twitter.com", "pic.twitter.com")
    ]
    text = x["text"]
    for link in links:
        text = text.replace(link, "")
    return Item(
        str(x["id"]),
        text.strip() or x["text"],
        int(x["created_timestamp"]),
        links,
        x["url"],
    )


def fx(s, old):
    url = (
        "https://api.fxtwitter.com/2/profile/"
        + quote(s["handle"], safe="")
        + "/statuses"
    )
    params = {}
    # since 의 엄격한 초 경계에서 같은 초의 다음 글을 놓치지 않는다.
    if old and old.get("ts"):
        params["since"] = max(0, old["ts"] - 1)
    entries = []
    seen = set()
    boundary = (old or {}).get("id")
    gap = ""
    for _ in range(100):
        response = requests.get(url, params=params, timeout=20)
        if response.status_code == 204 and "cursor" not in params and "since" in params:
            return [], old, ""
        response.raise_for_status()
        page = response.json()
        if page["code"] != 200 or not isinstance(page["results"], list):
            raise ValueError("invalid timeline")
        batch = [i for x in page["results"] if (i := xitem(s, x))]
        entries.extend(batch)
        if not boundary or any(i.uid == boundary for i in batch):
            break
        cursor = (page.get("cursor") or {}).get("bottom")
        if not cursor or cursor in seen:
            gap = "타임라인 끝에서 이전 ID 미발견 · 누락 가능"
            break
        seen.add(cursor)
        params = {"cursor": cursor}
    else:
        gap = "페이지 복구 한도 · 누락 가능"
    newest = max(entries, key=lambda i: int(i.uid), default=None)
    if boundary:
        entries = [i for i in entries if int(i.uid) > int(boundary)]
    checkpoint = (
        {"id": newest.uid, "ts": newest.published}
        if newest and (not boundary or int(newest.uid) > int(boundary))
        else old or {}
    )
    return entries, checkpoint, gap


class ArticleLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.links.extend(urls([dict(attrs).get("href")]))


def mail_item(raw, sender, uid):
    msg = email.message_from_bytes(raw, policy=policy.default)
    if parseaddr(str(msg.get("From", "")))[1].casefold() != sender.casefold():
        return None
    links = []
    for part in msg.walk():
        if part.get_content_disposition() == "attachment":
            continue
        if part.get_content_type() == "text/html":
            parser = ArticleLinks()
            parser.feed(part.get_content())
            links.extend(parser.links)
        elif part.get_content_type() == "text/plain":
            links.extend(urls(re.findall(r'https?://[^\s<>"]+', part.get_content())))
    # 뉴스레터 관리/추적 링크를 원문 기사로 오인하지 않는다.
    links = [
        u
        for u in links
        if urlsplit(u).hostname in ("www.washingtonpost.com", "washingtonpost.com")
        and re.search(r"/(?:20\d\d/|[^/]+/20\d\d-)", urlsplit(u).path)
    ]
    return Item(
        str(msg.get("Message-ID") or uid),
        str(msg["Subject"]),
        int(parsedate_to_datetime(str(msg["Date"])).timestamp()),
        list(dict.fromkeys(links))[:1],
    )


def imap(s, old):
    sender = required("WP_FROM")
    folder = required("WP_FOLDER")
    if any(c in folder for c in '"\r\n\\'):
        raise ValueError("unsupported folder")
    with imaplib.IMAP4_SSL(required("IMAP_HOST"), timeout=25) as client:
        client.login(required("IMAP_USER"), required("IMAP_PASSWORD"))
        if client.select('"' + folder + '"', readonly=True)[0] != "OK":
            raise ValueError("mailbox unavailable")
        validity = client.response("UIDVALIDITY")[1][0].decode()
        if old and old["validity"] != validity:
            # 새 UID 공간의 메시지는 Message-ID로 중복 제거한다.
            start = 0
            gap = "UIDVALIDITY 변경 · 보관된 메일 재검사"
        else:
            start = int((old or {}).get("uid", 0))
            gap = ""
        ok, data = client.uid(
            "search", None, "UID", f"{start + 1}:*", "FROM", '"' + sender + '"'
        )
        if ok != "OK":
            raise ValueError("mail search failed")
        ids = sorted(int(k) for k in data[0].split() if int(k) > start)
        entries = []
        if old:
            for k in ids[:50]:
                ok, msg = client.uid("fetch", str(k), "(BODY.PEEK[])")
                if ok != "OK":
                    raise ValueError("mail fetch failed")
                item = mail_item(
                    next(x[1] for x in msg if isinstance(x, tuple)), sender, k
                )
                if item:
                    entries.append(item)
                start = k
        elif ids:
            start = ids[-1]
        return entries, {"validity": validity, "uid": start}, gap


def dart(s, old):
    now = int(time.time())
    end = datetime.fromtimestamp(now, KST).date()
    start = datetime.fromtimestamp((old or {}).get("ts", now), KST).date()
    entries = []
    while start <= end:
        stop = min(start + timedelta(days=80), end)
        page = 1
        while True:
            r = requests.get(
                "https://opendart.fss.or.kr/api/list.json",
                params={
                    "crtfc_key": required("DART_KEY"),
                    "bgn_de": start.strftime("%Y%m%d"),
                    "end_de": stop.strftime("%Y%m%d"),
                    "page_no": page,
                    "page_count": 100,
                },
                timeout=20,
            )
            r.raise_for_status()
            payload = r.json()
            if payload["status"] == "013":
                break
            if payload["status"] != "000":
                raise ValueError("DART status " + str(payload["status"])[:3])
            for e in payload["list"]:
                uid = e["rcept_no"]
                day = datetime.strptime(e["rcept_dt"], "%Y%m%d").replace(tzinfo=KST)
                entries.append(
                    Item(
                        uid,
                        e["corp_name"] + " · " + e["report_nm"],
                        int(day.timestamp()),
                        ["https://dart.fss.or.kr/dsaf001/main.do?rcpNo=" + uid],
                        date_only=True,
                        company=e["corp_name"],
                    )
                )
            if page >= int(payload["total_page"]):
                break
            page += 1
        start = stop + timedelta(days=1)
    return entries, {"ts": now}, ""


ADAPTERS = {"rss": rss, "imap": imap, "dart": dart, "fx": fx, "bluesky": bluesky}


def error_label(exc):
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        return "HTTP " + str(exc.response.status_code)
    if isinstance(exc, ValueError) and str(exc).startswith("missing "):
        return str(exc)
    return type(exc).__name__


def protected_text(text, names):
    # Azure HTML 번역에서 수치와 단위, 등록된 고유명사의 원문 표기를 고정한다.
    number = r"(?:[$€£₩¥]\s*)?\d+(?:[.,]\d+)*(?:\s?(?:trillion|billion|million|thousand|percent|bps|USD|EUR|KRW|GBP|MW|GW|kWh|GHz|bn|tn|[kmbt])\b|%)?"
    patterns = [
        number,
        *(
            r"(?<!\w)" + re.escape(name) + r"(?!\w)"
            for name in sorted(names, key=len, reverse=True)
            if name
        ),
    ]
    pattern = re.compile("|".join(patterns), re.IGNORECASE)
    pieces = []
    values = []
    pos = 0
    for m in pattern.finditer(text):
        pieces.extend(
            [
                escape(text[pos : m.start()]),
                '<span class="notranslate">' + escape(m[0]) + "</span>",
            ]
        )
        values.append(m[0])
        pos = m.end()
    pieces.append(escape(text[pos:]))
    return "".join(pieces), values


def translate(text, lang, names=()):
    markup, required_values = protected_text(text, names)
    if not os.getenv("TRANSLATOR_KEY", "").strip():
        from .local_translation import translate_local

        return translate_local(text, lang, required_values)
    headers = {"Ocp-Apim-Subscription-Key": required("TRANSLATOR_KEY")}
    if os.getenv("TRANSLATOR_REGION"):
        headers["Ocp-Apim-Subscription-Region"] = os.environ["TRANSLATOR_REGION"]
    r = requests.post(
        "https://api.cognitive.microsofttranslator.com/translate",
        params={"api-version": "3.0", "from": lang, "to": "ko", "textType": "html"},
        headers=headers,
        json=[{"Text": markup}],
        timeout=20,
    )
    r.raise_for_status()
    parser = VisibleText()
    parser.feed(r.json()[0]["translations"][0]["text"])
    ko = "".join(parser.parts).strip()
    if any(v not in ko for v in required_values):
        raise ValueError("protected source value missing")
    if not ko or not re.search("[가-힣]", ko):
        raise ValueError("missing Korean translation")
    return ko
