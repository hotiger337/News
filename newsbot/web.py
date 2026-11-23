"""Loopback web UI for the same SQLite articles and ratings as the personal bot."""

import json
import re
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .core import Store
from .topics import rank


def article_url(item):
    for value in [item.get("post"), *item.get("links", [])]:
        if isinstance(value, str) and value.startswith(("https://", "http://")):
            return value
    return None


def live(db, owner, now):
    articles = []
    seen = set()
    for row in db.execute("""
        select i.id,i.data,i.ko,i.publisher,i.urgent,coalesce(v.content,0) as vote
        from items i left join votes v on v.item=i.id and v.owner=?
        where i.found>=? and i.state not in ('baseline','duplicate','superseded')
          and json_extract(i.data,'$.published') between ? and ?
        order by json_extract(i.data,'$.published') desc,i.id desc limit 1000""",
        (str(owner), now - 86400, now - 86400, now),
    ):
        item = json.loads(row["data"])
        url = article_url(item)
        if not url or url in seen:
            continue
        seen.add(url)
        articles.append({
            "id": row["id"], "title": row["ko"] or item["title"], "url": url,
            "source": row["publisher"], "summary": item.get("detail") or "",
            "date": datetime.fromtimestamp(item["published"], timezone.utc).isoformat(),
            "vote": row["vote"], "breaking": bool(row["urgent"]),
        })
    return {"articles": articles, "topics": rank(articles),
            "breaking": [a for a in articles if a["breaking"]],
            "asof": datetime.fromtimestamp(now, timezone.utc).isoformat(), "snapshot": False,
            "sources": len({a["source"] for a in articles}), "voting": True, "feed": True}


def record_vote(store, owner, body):
    if not isinstance(body, dict):
        raise ValueError
    n, url, value = (body.get(key) for key in ("id", "url", "value"))
    if type(n) is not int or not 0 < n <= 2**63-1 or type(value) is not int or value not in (-1, 0, 1):
        raise ValueError
    row = store.item(n)
    if not row or not isinstance(url, str) or article_url(json.loads(row["data"])) != url:
        raise LookupError
    store.vote(owner, n, {-1: "down", 0: "undo", 1: "up"}[value])
    return {"value": value}


def serve(database, cfg, owner):
    root = database.parent / (database.stem + "-archive")
    index = Path(__file__).with_name("web_index.html")

    class NewsHandler(BaseHTTPRequestHandler):
        def valid_host(self):
            return self.headers.get("Host") in ("localhost:8765", "127.0.0.1:8765")

        def respond(self, status, body, content_type="application/json; charset=utf-8"):
            if not isinstance(body, bytes):
                body = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if not self.valid_host():
                return self.respond(403, {"error": "localhost 전용"})
            path = urlsplit(self.path).path
            if path == "/":
                return self.respond(200, index.read_bytes(), "text/html; charset=utf-8")
            if path == "/api/live":
                with closing(sqlite3.connect(database)) as db:
                    db.row_factory = sqlite3.Row
                    result = live(db, owner, int(time.time()))
                return self.respond(200, result)
            if path == "/api/archive":
                days = [p.relative_to(root).with_suffix("").as_posix().replace("/", "-")
                        for p in root.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/[0-9][0-9].json")]
                return self.respond(200, sorted(days, reverse=True))
            match = re.fullmatch(r"/archive/([0-9]{4}/[0-9]{2}/[0-9]{2}\.json)", path)
            if match and (root / match[1]).is_file():
                return self.respond(200, (root / match[1]).read_bytes())
            self.respond(404, {"error": "찾을 수 없습니다."})

        def do_POST(self):
            if (not self.valid_host() or self.headers.get("Origin") != "http://" + self.headers["Host"]):
                return self.respond(403, {"error": "같은 사이트에서만 평가할 수 있습니다."})
            if self.path != "/api/vote":
                return self.respond(404, {})
            if self.headers.get_content_type() != "application/json":
                return self.respond(415, {})
            try:
                size = int(self.headers.get("Content-Length", ""))
                if not 0 < size <= 8192:
                    raise ValueError
                body = json.loads(self.rfile.read(size))
                store = Store(database, cfg)
                with closing(store.db):
                    result = record_vote(store, owner, body)
            except (ValueError, UnicodeError):
                return self.respond(400, {"error": "평가 형식을 확인하세요."})
            except LookupError:
                return self.respond(404, {"error": "원문과 일치하는 기사를 찾을 수 없습니다."})
            self.respond(200, result)

    with ThreadingHTTPServer(("127.0.0.1", 8765), NewsHandler) as server:
        server.serve_forever()
