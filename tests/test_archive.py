import json
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from newsbot.archive import export_completed
from newsbot.core import KST, SCHEMA


class DailyArchive(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dbpath = Path(tmp.name) / "store.sqlite3"
        self.root = Path(tmp.name) / "store-archive"
        self.db = sqlite3.connect(self.dbpath)
        self.addCleanup(self.db.close)
        self.db.executescript(SCHEMA)

    def add_article(self, date, hour, title):
        ts = int(datetime(2026, 10, date, hour, tzinfo=KST).timestamp())
        with self.db:
            self.db.execute("""insert into items(publisher,source,urgent,data,features)
                            values('A','news',1,?,?)""",
                            (json.dumps({"title": title, "published": ts, "links": ["https://a/b"]}),
                             json.dumps({"topic": ["정치"]})))

    def test_backfill_and_late_correction(self):
        self.add_article(5, 23, "첫 보도")
        self.add_article(6, 0, "다음날 보도")
        export_completed(self.dbpath, self.root, "2026-10-06")
        first = self.root / "2026/10/05.json"
        self.assertEqual(json.loads(first.read_text())["counts"]["breaking"], 1)
        self.assertFalse((self.root / "2026/10/06.json").exists())
        with self.db:
            self.db.execute("update items set ko='갱신' where id=1")
            self.db.execute("insert into social_reports values('2026-10-05 12:00','검증','[]',1,1)")
        export_completed(self.dbpath, self.root, "2026-10-07")
        data = json.loads(first.read_text())
        self.assertEqual(data["counts"], {"articles": 1, "breaking": 1, "social_reports": 1})
        self.assertEqual(data["articles"][0]["korean_title"], "갱신")
        self.assertEqual(data["topics"], {"정치": 1})
        self.assertEqual(data["social_reports"][0]["body"], "검증")
        self.assertTrue((self.root / "2026/10/06.json").exists())

    def test_retry_after_file_error(self):
        self.add_article(5, 1, "보도")
        with patch("newsbot.archive.os.replace", side_effect=OSError), self.assertRaises(OSError):
            export_completed(self.dbpath, self.root, "2026-10-06")
        self.assertEqual(list((self.root / "2026/10").iterdir()), [])
        export_completed(self.dbpath, self.root, "2026-10-06")
        self.assertTrue((self.root / "2026/10/05.json").exists())
