"""기사 수집과 발송 예약은 같은 SQLite 트랜잭션에서 확정한다."""

import json
import math
import os
import re
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


def url_key(url):
    p = urlsplit(url)
    args = [
        (k, v)
        for k, v in parse_qsl(p.query, keep_blank_values=True)
        if not k.startswith("utm_")
    ]
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path, urlencode(args), ""))


def story_keys(item):
    # Remove editorial wrapping, keep amounts, signs, names and word order.
    title = re.sub(
        r"^\s*(?:\[속보\]|\[breaking(?: news)?\]|breaking(?: news)?\s*:)\s*",
        "",
        item.title,
        flags=re.I,
    )
    title = re.sub(r"""[‘’“”"'…]""", "", title.casefold())
    title = " ".join(title.rstrip(". ").split())
    keys = []
    link = item.evidence or (item.links[0] if len(item.links) == 1 else "")
    if link and urlsplit(link).path.strip("/"):
        keys.append("url:" + url_key(link))
    if len(title) >= 20 and len(title.split()) >= 4:
        keys.append("title:" + title)
    return keys


@dataclass
class Item:
    uid: str
    title: str
    published: int
    links: list = field(default_factory=list)
    post: str = ""
    categories: list = field(default_factory=list)
    origin: str = ""
    # 명시적인 원보도/공시 URL로만 사건을 연결한다.
    evidence: str = ""
    date_only: bool = False
    detail: str = ""
    company: str = ""


def breaking(s, item):
    rule = s.get("rule", "none")
    return rule == "dedicated" or (
        rule == "marker"
        and (
            bool(re.search(s["marker"], item.title))
            or any(
                c.casefold() in s.get("category_markers", []) for c in item.categories
            )
        )
    )


def features(s, item, cfg):
    text = (item.title + " " + " ".join(item.categories)).casefold()

    def matches(table):
        return [
            name
            for name, aliases in cfg[table].items()
            if any(a.casefold() in text for a in [name, *aliases])
        ]

    companies = sorted(
        set(matches("companies") + ([s["company"]] if s.get("company") else []))
    )
    if item.company:
        mapped = next(
            (
                name
                for name, aliases in cfg["companies"].items()
                if item.company.casefold() in {a.casefold() for a in [name, *aliases]}
            ),
            item.company,
        )
        companies = sorted(set([*companies, mapped]))
    kind = s.get("kind", "뉴스")
    return dict(
        kind=[kind],
        topic=matches("topics"),
        company=companies,
        company_kind=[c + "·" + kind for c in companies],
    )


SCHEMA = """
CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY,cursor TEXT,success INTEGER,error TEXT,gap TEXT);
CREATE TABLE IF NOT EXISTS items(id INTEGER PRIMARY KEY,publisher TEXT,source TEXT,data TEXT,features TEXT,
 urgent INTEGER,found INTEGER,previous INTEGER,related INTEGER,ko TEXT,translated INTEGER,state TEXT,
 due INTEGER DEFAULT 0,attempt INTEGER DEFAULT 0,error TEXT,started INTEGER,sent INTEGER,message TEXT,channel TEXT,reason TEXT);
CREATE TABLE IF NOT EXISTS source_errors(source TEXT PRIMARY KEY,at INTEGER,label TEXT);
CREATE TABLE IF NOT EXISTS identities(publisher TEXT,k TEXT,current INTEGER,PRIMARY KEY(publisher,k));
CREATE TABLE IF NOT EXISTS story_keys(k TEXT, item INTEGER, PRIMARY KEY(k,item));
CREATE TABLE IF NOT EXISTS observations(source TEXT,uid TEXT,version TEXT,PRIMARY KEY(source,uid));
CREATE TABLE IF NOT EXISTS evidence(url TEXT,item INTEGER,PRIMARY KEY(url,item));
CREATE TABLE IF NOT EXISTS votes(owner TEXT,item INTEGER,content INTEGER DEFAULT 0,batch INTEGER DEFAULT 0,PRIMARY KEY(owner,item));
CREATE TABLE IF NOT EXISTS interests(name TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY,v TEXT);
CREATE TABLE IF NOT EXISTS rankings(run TEXT,item INTEGER,score REAL,parts TEXT,decision TEXT);
CREATE INDEX IF NOT EXISTS queue ON items(state,due);
"""


class Store:
    def __init__(self, path, cfg):
        Path(path).parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.cfg = cfg
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("pragma journal_mode=WAL")
        self.db.executescript(SCHEMA)
        with self.db:
            if not self.db.execute(
                "select 1 from meta where k='initialized'"
            ).fetchone():
                self.db.executemany(
                    "insert or ignore into interests values(?)",
                    [(v,) for v in cfg["settings"]["initial_interests"]],
                )
                self.db.execute("insert into meta values('initialized','1')")

    def item(self, n):
        return self.db.execute("select * from items where id=?", (n,)).fetchone()

    def ingest(self, s, entries, cursor, gap="", now=None):
        now = int(time.time()) if now is None else now
        old = self.db.execute(
            "select cursor from sources where id=?", (s["id"],)
        ).fetchone()
        baseline = not old or old[0] is None
        with self.db:
            for item in sorted(entries, key=lambda i: i.published):
                version = json.dumps(asdict(item), ensure_ascii=False, sort_keys=True)
                # 폴링 창에 남은 구버전이 다른 채널의 후속 보도를 되돌리지 않게 한다.
                changed = self.db.execute(
                    """insert into observations values(?,?,?)
                    on conflict(source,uid) do update set version=excluded.version
                    where version<>excluded.version""",
                    (s["id"], item.uid, version),
                ).rowcount
                if changed:
                    self.save(s, item, baseline, now)
            self.db.execute(
                """insert into sources values(?,?,?,null,?) on conflict(id) do update set
                cursor=excluded.cursor,success=excluded.success,error=null,gap=coalesce(nullif(excluded.gap,''),sources.gap)""",
                (s["id"], json.dumps(cursor), now, gap),
            )

    def save(self, s, item, baseline, now):
        publisher = s["publisher"]
        keys = [s["id"] + ":" + item.uid] + [
            url_key(u) for u in [item.post, *item.links] if u
        ]
        # 기사 목록을 인용하는 X 글을 개별 기사 모두와 동일시하지 않는다.
        if len(item.links) > 1:
            keys = keys[: 1 + bool(item.post)]
        prev = None
        for key in keys:
            found = self.db.execute(
                "select current from identities where publisher=? and k=?",
                (publisher, key),
            ).fetchone()
            if found:
                prev = self.item(found[0])
                break
        urgent = breaking(s, item)
        data = dict(
            asdict(item),
            lang=s["lang"],
            role=s.get("role", ""),
            handle=s.get("handle", ""),
        )
        if prev:
            prior = json.loads(prev["data"])
            distinct_post = item.post and prior["post"] and item.post != prior["post"]
            update = re.search(
                r"정정|철회|공식 확인|correct(?:ion|ed)|retract(?:ion|ed)|officially confirmed",
                item.title,
                re.I,
            )
            old_title = prior.get("headline", prior["title"])
            detail_change = (
                prev["source"] == s["id"]
                and prior.get("detail")
                and item.detail
                and prior["detail"] != item.detail
                and old_title == item.title
            )
            if detail_change:
                data["headline"] = item.title
                data["title"] = item.title + " · " + item.detail
            same = (
                not detail_change
                and not distinct_post
                and (
                    old_title == item.title
                    or (prev["source"] != s["id"] and not update)
                )
            )
            if same and (not urgent or prev["urgent"] or prev["state"] == "baseline"):
                prior["post"] = prior["post"] or item.post
                prior["handle"] = prior["handle"] or data["handle"]
                prior["links"] = list(dict.fromkeys(prior["links"] + item.links))
                self.db.execute(
                    "update items set data=? where id=?",
                    (json.dumps(prior), prev["id"]),
                )
                self.bind(publisher, keys, prev["id"])
                return
            urgent = urgent or bool(prev["urgent"])
        evidence = item.evidence or (item.links[0] if len(item.links) == 1 else "")
        related = (
            self.db.execute(
                """select e.item from evidence e join items i on i.id=e.item
            where e.url=? and i.publisher<>? order by e.item desc limit 1""",
                (url_key(evidence), publisher),
            ).fetchone()
            if evidence
            else None
        )
        state = (
            "baseline"
            if baseline
            else ("ready" if s["lang"] == "ko" else "translate")
            if urgent
            else "candidate"
        )
        collect_all = self.cfg["settings"].get("collect_all", False)
        keys_for_story = story_keys(item) if collect_all and not baseline else []
        representative = None
        for key in keys_for_story:
            match = self.db.execute(
                """select i.* from story_keys k join items i on i.id=k.item
                  where k.k=? and i.found>=? and i.state in ('sent','ready','sending','translate','uncertain','retry')
                  order by i.id limit 1""",
                (key, now - 86400),
            ).fetchone()
            if not match or prev:
                continue
            first = json.loads(match["data"])
            if first["title"] != item.title and (
                re.search(
                    r"정정|철회|확정|공식 확인|correct|retract|confirm",
                    item.title,
                    re.I,
                )
                or re.findall(r"[-+]?\d+(?:[.,]\d+)*", first["title"])
                != re.findall(r"[-+]?\d+(?:[.,]\d+)*", item.title)
            ):
                continue
            if first.get("detail") and item.detail and first["detail"] != item.detail:
                continue
            representative = match
            break
        if (
            representative
            and urgent
            and not representative["urgent"]
            and representative["state"] in ("ready", "retry")
        ):
            self.db.execute(
                "update items set state='superseded' where id=?",
                (representative["id"],),
            )
            representative = None
        if representative:
            state = "duplicate"
            related = (representative["id"],)
        elif collect_all and state == "candidate":
            state = "ready"
            data["all_sources"] = True
        n = self.db.execute(
            """insert into items(publisher,source,data,features,urgent,found,previous,related,state)
          values(?,?,?,?,?,?,?,?,?)""",
            (
                publisher,
                s["id"],
                json.dumps(data, ensure_ascii=False),
                json.dumps(features(s, item, self.cfg), ensure_ascii=False),
                int(urgent),
                now,
                prev["id"] if prev else None,
                related[0] if related else None,
                state,
            ),
        ).lastrowid
        if keys_for_story:
            target = representative["id"] if representative else n
            self.db.executemany(
                "insert or ignore into story_keys values(?,?)",
                [(k, target) for k in keys_for_story],
            )
        if collect_all and not baseline and not urgent:
            score, parts, _ = self.score(self.item(n), now)
            self.log_score(
                "intake",
                self.item(n),
                score,
                parts,
                "중복" if representative else "전체 수신",
            )
        self.bind(publisher, keys, n)
        if evidence:
            self.db.execute(
                "insert or ignore into evidence values(?,?)", (url_key(evidence), n)
            )
        if (
            prev
            and not prev["urgent"]
            and prev["state"] in ("candidate", "ready", "retry")
            and not baseline
        ):
            self.db.execute(
                "update items set state='superseded' where id=?", (prev["id"],)
            )
        if state == "candidate":
            self.evaluate(self.item(n), now, "intake", True)

    def bind(self, pub, keys, n):
        self.db.executemany(
            "insert or replace into identities values(?,?,?)",
            [(pub, k, n) for k in keys],
        )

    def vote(self, owner, n, action):
        if not self.item(n):
            raise ValueError("unknown item")
        with self.db:
            if action == "undo":
                self.db.execute(
                    "delete from votes where owner=? and item=?", (str(owner), n)
                )
                return
            col, val = {
                "up": ("content", 1),
                "down": ("content", -1),
                "batch": ("batch", 1),
            }[action]
            self.db.execute(
                f"""insert into votes(owner,item,{col}) values(?,?,?) on conflict(owner,item)
                                do update set {col}=excluded.{col}""",
                (str(owner), n, val),
            )

    def score(self, row, now):
        f = json.loads(row["features"])
        data = json.loads(row["data"])
        weights = self.cfg["weights"]
        counts = {(k, v): [0, 0] for k, vs in f.items() for v in vs}
        batch = False
        for vote in self.db.execute(
            "select content,batch,features from votes join items on items.id=votes.item"
        ):
            rated = json.loads(vote["features"])
            for (k, v), pair in counts.items():
                if v in rated[k]:
                    pair[0] += vote["content"] == 1
                    pair[1] += vote["content"] == -1
                    if k in ("topic", "company_kind") and vote["batch"]:
                        batch = True
        parts = {
            f"{k} {v} 평가(+{p}/-{m})": weights[k] * (p - m) / (p + m + 4)
            for (k, v), (p, m) in counts.items()
            if p or m
        }
        corpus = " ".join(
            [data["title"], *f["topic"], *f["company"], *f["kind"]]
        ).casefold()
        for (name,) in self.db.execute("select name from interests"):
            if name.casefold() in corpus:
                parts["등록 관심사 " + name] = weights["interest"]
        parts["최신성"] = weights["freshness"] * math.exp(
            -max(0, now - data["published"]) / 86400
        )
        return sum(parts.values()), parts, batch

    def log_score(self, run, row, score, parts, decision):
        self.db.execute(
            "insert into rankings values(?,?,?,?,?)",
            (run, row["id"], score, json.dumps(parts, ensure_ascii=False), decision),
        )

    def queue(self, row, parts):
        reason = max(parts, key=parts.get)
        if " 평가(" in reason:
            reason = (
                reason.split(" 평가(")[0].split(" ", 1)[-1]
                + " 자료에 남긴 긍정 평가를 반영했습니다."
            )
        elif reason.startswith("등록 관심사 "):
            reason = (
                "등록한 관심사 ‘"
                + reason.removeprefix("등록 관심사 ")
                + "’와 일치합니다."
            )
        else:
            reason = "최근에 게시된 자료입니다."
        self.db.execute(
            "update items set state='ready',reason=? where id=?", (reason, row["id"])
        )

    def evaluate(self, row, now, run, instant):
        score, parts, batch = self.score(row, now)
        send = (
            instant and not batch and score >= self.cfg["settings"]["instant_threshold"]
        )
        self.log_score(run, row, score, parts, "즉시" if send else "보류")
        if send:
            self.queue(row, parts)
        return score, parts

    def digest(self, now):
        cfg = self.cfg["settings"]
        if cfg.get("collect_all"):
            return
        local = datetime.fromtimestamp(now, KST)
        day = local.date().isoformat()
        if local.strftime("%H:%M") < cfg["digest_time"]:
            return
        with self.db:
            if not self.db.execute(
                "insert or ignore into meta values(?,?)", ("digest:" + day, day)
            ).rowcount:
                return
            ranked = []
            for r in self.db.execute("select * from items where state='candidate'"):
                if (
                    json.loads(r["data"])["published"]
                    < now - cfg["max_age_hours"] * 3600
                ):
                    continue
                score, parts, _ = self.score(r, now)
                ranked.append((score, r["id"], r, parts))
            ranked.sort(reverse=True, key=lambda x: x[:2])
            top = [x for x in ranked if x[0] > x[3]["최신성"]][: cfg["top"]]
            themes = {
                v for _, _, r, _ in top for v in json.loads(r["features"])["topic"]
            }
            neighbors = {
                v
                for t in themes
                for v in self.cfg.get("adjacent_topics", {}).get(t, [])
            } - themes
            adjacent = next(
                (
                    x
                    for x in ranked
                    if x not in top
                    if set(json.loads(x[2]["features"])["topic"]) & neighbors
                ),
                None,
            )
            selected = {x[1] for x in top}
            if cfg["adjacent"] and adjacent:
                selected.add(adjacent[1])
            for score, n, r, parts in ranked:
                self.log_score(
                    day, r, score, parts, "선정" if n in selected else "보류"
                )
                if n in selected:
                    self.queue(r, parts)


def render(store, r):
    d = json.loads(r["data"])
    label = (
        ("국내 속보" if d["lang"] == "ko" else "해외 속보")
        if r["urgent"]
        else "새 자료"
        if d.get("all_sources")
        else "관심 자료"
    )
    lines = [
        f"[{label}] {r['publisher']}" + (" · @" + d["handle"] if d["handle"] else "")
    ]
    if r["ko"]:
        lines.append(r["ko"])
    lines.append(d["title"])
    lines.append(
        datetime.fromtimestamp(d["published"], KST).strftime(
            "%Y-%m-%d · 한국시간 (시각 미제공)"
            if d.get("date_only")
            else "%Y-%m-%d %H:%M · 한국시간"
        )
    )
    if d["role"] or d["origin"]:
        lines.append(" · ".join(filter(None, [d["role"], d["origin"]])))

    def reference(old):
        guild = os.getenv("DISCORD_GUILD_ID")
        if guild and old["channel"] and old["message"]:
            return f"https://discord.com/channels/{guild}/{old['channel']}/{old['message']}"
        return f"#{old['id']}"

    if r["previous"]:
        old = store.item(r["previous"])
        lines.append(
            f"갱신 {reference(old)} · 이전: "
            + (
                json.loads(old["data"]).get("detail")
                if d.get("headline")
                else json.loads(old["data"])["title"]
            )[:200]
        )
    if r["related"]:
        lines.append(f"같은 원문을 인용한 보도 {reference(store.item(r['related']))}")
    if r["reason"]:
        lines.append("추천 근거 · " + r["reason"])
    links = list(
        dict.fromkeys(u for u in [d["post"], *d["links"], d.get("evidence")] if u)
    )
    footer = "".join("\n<" + u + ">" for u in links)
    room = 1998 - len(footer)
    if room < 200:
        raise ValueError("URL length exceeds Discord limit")
    # 긴 X 본문에서도 시각/출처/링크 영역을 확보한다.
    budget = max(
        50,
        (room - sum(map(len, lines[2 if not r["ko"] else 3 :])) - len(lines[0]) - 20)
        // (2 if r["ko"] else 1),
    )
    for i in range(1, 3 if r["ko"] else 2):
        if len(lines[i]) > budget:
            lines[i] = lines[i][: budget - 1] + "…"
    return "\n".join(lines) + footer
