import tempfile
import tomllib
import unittest
from pathlib import Path

from newsbot.core import Item, Store
from newsbot.delivery import deliver

CFG = tomllib.loads(Path("sources.toml").read_text())
SBS = CFG["sources"][0]


class Transport:
    def __init__(self):
        self.messages = {}

    def channel(self, row):
        return 123

    async def send(self, channel, n, body):
        self.messages[n] = body
        return n + 100

    async def find(self, row):
        return (row["id"] + 100, 100) if row["id"] in self.messages else None


class Flow(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = self.tmp.name + "/state.db"
        self.store = Store(self.path, CFG)
        self.db = self.store.db
        self.addCleanup(self.db.close)
        self.store.ingest(SBS, [], {}, now=0)

    async def test_first_pipeline(self):
        s = self.store
        rows = [Item("1", "[속보] 스포츠 결과", 10), Item("2", "일반 기사", 10)]
        s.ingest(SBS, rows, {}, now=11)
        channel = Transport()
        await deliver(s, s.item(1), channel)
        self.assertIn("스포츠", channel.messages[1])
        self.assertEqual(s.item(2)["state"], "candidate")
        s.vote(42, 1, "down")
        s.ingest(SBS, [*rows, Item("3", "[속보] 스포츠 결승", 15)], {}, now=16)
        await deliver(s, s.item(3), channel)
        self.assertEqual(list(channel.messages), [1, 3])
        source = dict(SBS, kind="공시")
        s.ingest(source, [Item("4", "코어위브 공시", 20)], {}, now=20)
        s.ingest(SBS, [Item("5", "CRWV 시황", 20)], {}, now=20)
        before = [s.score(s.item(n), 20)[0] for n in (4, 5)]
        s.vote(42, 4, "up")
        s.vote(42, 4, "up")
        s.vote(42, 5, "down")
        self.assertGreater(s.score(s.item(4), 20)[0], s.score(s.item(5), 20)[0])
        s.vote(42, 4, "undo")
        s.vote(42, 5, "undo")
        self.assertEqual(before, [s.score(s.item(n), 20)[0] for n in (4, 5)])

    async def test_collect_all_with_representative(self):
        import copy

        cfg = copy.deepcopy(CFG)
        cfg["settings"]["collect_all"] = True
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp + "/state.db", cfg)
            try:
                other = dict(SBS, id="other", publisher="Other")
                title = "오늘 전국 각 지역에 새로운 체육 시설 개장"
                store.ingest(SBS, [Item("old", "지난 기사", 1)], {}, now=1)
                store.ingest(other, [], {}, now=1)
                store.ingest(SBS, [Item("a", title, 2)], {}, now=2)
                store.vote(1, 2, "down")
                store.ingest(
                    other,
                    [Item("b", title, 3), Item("c", "전혀 다른 분야의 소식", 4)],
                    {},
                    now=4,
                )
                self.assertEqual(
                    [store.item(i)["state"] for i in range(1, 5)],
                    ["baseline", "ready", "duplicate", "ready"],
                )
                self.assertEqual(store.item(3)["related"], 2)
            finally:
                store.db.close()
