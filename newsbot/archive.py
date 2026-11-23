"""Export completed Korean calendar days from the bot's database."""

import asyncio
import json
import os
import sqlite3
from contextlib import closing
from datetime import date, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile

from .core import KST
from .topics import rank


# SQLite date() applies the fixed modern Korean offset to the UTC Unix timestamp.
PUBLISHED_DAY = "date(json_extract(data,'$.published'),'unixepoch','+9 hours')"
SLOT_DAY = "substr(slot,1,10)"


def setup(db):
    db.execute("""create table if not exists archive_pending(
                  day text primary key not null, revision integer not null)""")
    # A Telegram-only database may not have run the social reporter yet.
    db.execute("""create table if not exists social_reports(
                  slot text primary key,body text,urls text,attempted integer,sent integer)""")
    db.execute(f"create index if not exists archive_items_day on items({PUBLISHED_DAY})")
    for table, column, expression, tracked in [
        ("items", "data", PUBLISHED_DAY, "data,publisher,source,urgent,ko,features"),
        ("social_reports", "slot", SLOT_DAY, "slot,body,urls,sent"),
    ]:
        for action, refs in [("insert", ["new"]), ("update", ["old", "new"]), ("delete", ["old"])]:
            event = f"update of {tracked}" if action == "update" else action
            marks = []
            for ref in refs:
                day = expression.replace(column, f"{ref}.{column}")
                marks.append(f"""insert into archive_pending(day,revision)
                    select {day},1 where {day} is not null
                    on conflict(day) do update set revision=revision+1;""")
            db.execute(f"""create trigger if not exists archive_{table}_{action}
                after {event} on {table} begin {' '.join(marks)} end""")
        db.execute(f"""insert or ignore into archive_pending
                       select distinct {expression},1 from {table} where {expression} is not null""")
    db.execute("insert into meta values('daily_archive_schema','1')")


def contents(db, day):
    articles = []
    topic_counts = {}
    breaking_count = 0
    for row in db.execute(
        f"""select id,publisher,source,urgent,ko,data,features from items where {PUBLISHED_DAY}=?
            order by json_extract(data,'$.published'),id""", (day,)
    ):
        item = json.loads(row["data"])
        topics = json.loads(row["features"]).get("topic", [])
        for topic in set(topics):
            topic_counts[topic] = topic_counts.get(topic, 0) + 1
        urgent = bool(row["urgent"])
        breaking_count += urgent
        stamp = datetime.fromtimestamp(item["published"], KST).isoformat()
        urls = list(dict.fromkeys(filter(None, [item.get("post"), *item.get("links", [])])))
        articles.append({
            "id": row["id"], "published": stamp, "date": stamp,
            "publisher": row["publisher"], "source": row["publisher"], "source_id": row["source"],
            "title": row["ko"] or item["title"], "original_title": item["title"],
            "korean_title": row["ko"], "breaking": urgent, "topics": topics,
            "urls": urls, "url": next((u for u in urls if u.startswith(("https://", "http://"))), ""),
            "summary": item.get("detail") or "",
        })
    reports = [{"slot": row["slot"], "body": row["body"], "urls": json.loads(row["urls"]),
                "sent": bool(row["sent"])}
               for row in db.execute("""select * from social_reports
                   where slot>=? and slot<? and body<>'' order by slot""", (day, day + "Z"))]
    unique = {a["url"]: a for a in articles if a["url"]}
    return {"date": day, "timezone": "Asia/Seoul", "articleCount": len(articles),
            "keywords": rank(list(unique.values())),
            "counts": {"articles": len(articles), "breaking": breaking_count,
                       "social_reports": len(reports)},
            "topics": dict(sorted(topic_counts.items(), key=lambda v: (-v[1], v[0]))),
            "articles": articles, "social_reports": reports}


def write_file(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    with NamedTemporaryFile(mode="w", dir=path.parent, encoding="utf-8", delete=False) as f:
        tmp = Path(f.name)
        try:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
            f.close()
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)


def export_completed(database, directory, today):
    with closing(sqlite3.connect(database, timeout=10)) as db:
        db.row_factory = sqlite3.Row
        if not db.execute("select 1 from meta where k='daily_archive_schema'").fetchone():
            with db:
                setup(db)
        for row in db.execute("select day,revision from archive_pending where day<? order by day",
                              (today,)).fetchall():
            day = row["day"]
            parsed = date.fromisoformat(day)
            path = directory / parsed.strftime("%Y/%m/%d.json")
            with db:
                db.execute("begin")
                payload = contents(db, day)
            if payload["articles"] or payload["social_reports"]:
                write_file(path, payload)
            else:
                path.unlink(missing_ok=True)
            with db:
                # If ingestion changed this date during the export, leave it pending.
                db.execute("delete from archive_pending where day=? and revision=?", (day, row["revision"]))


async def archive_step(store):
    path = Path(store.db.execute("pragma database_list").fetchone()[2])
    directory = path.parent / (path.stem + "-archive")
    await asyncio.to_thread(export_completed, path, directory, datetime.now(KST).date().isoformat())
