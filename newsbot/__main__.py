import argparse
import asyncio
import fcntl
import json
import os
import sqlite3
import time
import tomllib
from pathlib import Path

from dotenv import load_dotenv

from .bot import NewsBot
from .core import Store, breaking
from .sources import ADAPTERS, error_label, required


def main():
    os.umask(0o077)
    load_dotenv(".env")
    p = argparse.ArgumentParser()
    p.add_argument(
        "command", choices=["run", "probe", "backup", "compare", "telegram-id"]
    )
    p.add_argument("argument", nargs="?")
    args = p.parse_args()
    if args.command == "telegram-id":
        from .telegram import identify

        identify()
        return
    cfg = tomllib.loads(Path(os.getenv("NEWS_CONFIG", "sources.toml")).read_text())
    dbpath = Path(os.getenv("NEWS_DB", "state/news.sqlite3")).resolve()
    if args.command == "probe":
        selected = [
            s
            for s in cfg["sources"]
            if s["id"] == args.argument
            or (not args.argument and s.get("enabled", True))
        ]
        if not selected:
            p.error("출처 ID를 확인하세요")
        failed = False
        for s in selected:
            try:
                entries, _, gap = ADAPTERS[s["adapter"]](s, None)
                print(
                    s["id"],
                    len(entries),
                    "건 / 표식·전용 채널 판정",
                    sum(breaking(s, i) for i in entries),
                    gap,
                )
            except Exception as exc:
                print(s["id"], error_label(exc))
                failed = True
        raise SystemExit(int(failed))
    if args.command == "backup":
        if not args.argument or not dbpath.is_file():
            p.error("기존 DB와 새 백업 파일 경로가 필요합니다")
        target = Path(args.argument)
        target.touch(mode=0o600, exist_ok=False)
        with sqlite3.connect(dbpath) as src, sqlite3.connect(target) as dest:
            src.backup(dest)
        return
    dbpath.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock = open(str(dbpath) + ".lock", "a")
    if args.command == "run":
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    store = Store(dbpath, cfg)
    if args.command == "compare":
        # 동일 후보·평가 스냅샷에서 두 설정의 점수와 기여도를 나란히 계산한다.
        other = tomllib.loads(Path(args.argument).read_text())
        now = int(time.time())
        for row in store.db.execute(
            "select * from items where urgent=0 and state!='baseline' order by id"
        ):
            store.cfg = cfg
            a = store.score(row, now)
            store.cfg = other
            b = store.score(row, now)
            print(
                json.dumps(dict(id=row["id"], old=a[:2], new=b[:2]), ensure_ascii=False)
            )
        return
    if os.getenv("TELEGRAM_BOT_TOKEN"):
        from .telegram import Telegram

        asyncio.run(Telegram(store).run())
        return
    NewsBot(store).run(required("DISCORD_TOKEN"), log_handler=None)


if __name__ == "__main__":
    main()
