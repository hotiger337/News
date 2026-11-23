import json
import tempfile
import unittest
from pathlib import Path

from newsbot.core import Store
from newsbot.web import live, record_vote


class PersonalWeb(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.s = Store(Path(tmp.name) / 'news.sqlite3', {"settings": {"initial_interests": []}})
        self.addCleanup(self.s.db.close)
        self.now = 1795350000
        for n, state in enumerate(['reviewed','duplicate','baseline','superseded','candidate'], 1):
            data = json.dumps({"title":"반도체 공장 발표", "links":[f"https://a.example/{n}"],
                               "published":self.now - n, "detail":"요약"})
            self.s.db.execute("""insert into items(id,data,state,found,urgent,publisher,features)
                                 values(?,?,?,?,?,?,?)""", (n,data,state,self.now,n==1,'매체','{}'))
        self.s.db.commit()

    def test_feed_ranking_and_existing_owner_vote(self):
        self.s.vote(100, 1, "down")
        page = live(self.s.db, 100, self.now)
        self.assertEqual([a["id"] for a in page["articles"]], [1,5])
        self.assertEqual(page["articles"][0]["vote"], -1)
        self.assertEqual([a["id"] for a in page["breaking"]], [1])
        self.assertEqual(page["topics"][0]["count"], 2)
        body = {"id":1, "url":page["articles"][0]["url"], "value":1}
        self.assertEqual(record_vote(self.s,100,body), {"value":1})
        rows = self.s.db.execute("select owner,item,content from votes").fetchall()
        self.assertEqual([tuple(r) for r in rows],[("100",1,1)])
        record_vote(self.s,100,dict(body, value=0))
        self.assertEqual(self.s.db.execute("select count(*) from votes").fetchone()[0],0)

    def test_reject_wrong_article_binding(self):
        with self.assertRaises(LookupError):
            record_vote(self.s,100,{"id":1,"url":"https://other","value":1})
        with self.assertRaises(ValueError):
            record_vote(self.s,100,{"id":1,"url":"https://a.example/1","value":True})
