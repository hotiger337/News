import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from newsbot import review
from newsbot.core import Store
from newsbot.site_export import CURSOR, sync_step
from newsbot.web import live, record_vote


class ActualDeliveries(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        cfg = {"settings": {"initial_interests": []}, "sources": [
            {"id":"sbs", "publisher":"SBS", "adapter":"rss", "url":"https://news.sbs.co.kr/news/rss"},
            {"id":"mail", "publisher":"MAIL", "adapter":"imap"}]}
        self.s = Store(Path(self.tmp.name)/"x.db", cfg)
        self.addCleanup(self.s.db.close)
        self.db = self.s.db
        self.db.executescript(review.SCHEMA)
        self.db.execute("insert into reviews(slot,state) values(1,'sent'),(2,'uncertain')")
        for i, (source,state,urgent,channel) in enumerate([
            ("sbs","sent",1,"12"), ("sbs","reviewed",0,None), ("sbs","reviewed",0,None),
            ("sbs","sent",1,"different"), ("mail","sent",1,"12"), ("sbs","uncertain",1,"12"),
            ("sbs","reviewed",0,None), ("sbs","reviewed",0,None)],1):
            link = f"https://news.sbs.co.kr/a/{i}?utm_source=rss"
            if i == 7:
                link = "https://elsewhere.test/article/1"
            data = {"title":f"원제목 {i}","published":995,"links":[link]}
            self.db.execute("""insert into items(id,source,state,urgent,channel,message,sent,data,publisher,found)
                values(?,?,?,?,?,'42',1000,?,'SBS',1000)""",(i,source,state,urgent,channel,json.dumps(data)))
            if i in (2,3,7):
                self.db.execute("insert into review_items values(?,?,?)", (2 if i == 3 else 1,i,i))
        self.db.commit()

    async def test_checkpoint_failure_and_retry(self):
        with patch.dict("os.environ", NEWS_SITE_TOKEN="test"), patch("newsbot.site_export.time.time", return_value=1002):
            with patch("newsbot.site_export.push", side_effect=RuntimeError), self.assertRaises(RuntimeError):
                await sync_step(self.s,12)
            self.assertLess(json.loads(self.db.execute("select v from meta where k=?", (CURSOR,)).fetchone()[0])[0], 1000)
            with patch("newsbot.site_export.push") as push:
                await sync_step(self.s,12)
                await sync_step(self.s,12)
        push.assert_called_once()
        articles = push.call_args.args[0]
        self.assertEqual([x["title"] for x in articles], ["원제목 1","원제목 2"])
        self.assertEqual({k for k in articles[0]}, {"source","title","originalTitle","url","date","summary","deliveredAt","breaking"})

    def test_local_provenance(self):
        with patch.dict("os.environ", TELEGRAM_BREAKING_CHAT_ID="12"):
            feed = live(self.db,12,1002,self.s.cfg)
        self.assertEqual({x["id"] for x in feed["telegramArticles"]}, {1,2})
        self.assertEqual({x["id"] for x in feed["articles"] if x["telegram"]}, {1,2})
        article = feed["telegramArticles"][0]
        self.assertEqual(record_vote(self.s,12,{**article,"value":1}),{"value":1})
