"""Collect public channel feeds and exact citations in trusted RSS articles."""

import calendar
import json
import time
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from urllib.parse import parse_qs, urljoin, urlsplit

import feedparser
import requests


def safe_page(url, domains):
    try:
        p = urlsplit(url)
        return (p.scheme == "https" and not p.username and not p.password
                and p.port in (None, 443) and p.path not in ("", "/")
                and not re.search(r"[\s<>\\]", url)
                and any(p.hostname == d or (p.hostname or "").endswith("." + d) for d in domains))
    except ValueError:
        return False


def read_page(url, domains):
    for _ in range(4):
        if not safe_page(url, domains):
            raise ValueError("untrusted URL")
        with requests.get(url, timeout=(4, 8), stream=True, allow_redirects=False,
                          headers={"User-Agent": "Newsbot/1.0 (RSS citation reader)"}) as resp:
            if resp.is_redirect:
                url = urljoin(url, resp.headers["Location"])
                continue
            resp.raise_for_status()
            data = bytearray()
            for chunk in resp.iter_content(65536):
                data.extend(chunk)
                if len(data) > 3_000_000:
                    raise ValueError("page too large")
            return url, bytes(data)
    raise ValueError("too many redirects")


def social_url(url):
    try:
        p = urlsplit(url)
        host = p.hostname
    except ValueError:
        return None
    if p.scheme not in ("https", "http") or p.username or p.password:
        return None
    if host in ("www.reddit.com", "reddit.com", "old.reddit.com"):
        match = re.fullmatch(r"/(?:r/[\w]+/)?comments/([a-z0-9]+)(?:/[^/]*)?/?", p.path)
        if match:
            return "https://www.reddit.com/comments/" + match[1]
    uid = ""
    if host == "youtu.be":
        uid = p.path.strip("/")
    elif host in ("youtube.com", "www.youtube.com", "www.youtube-nocookie.com", "m.youtube.com"):
        if p.path == "/watch":
            uid = parse_qs(p.query).get("v", [""])[0]
        elif p.path.startswith(("/embed/", "/shorts/", "/live/")):
            uid = p.path.split("/")[-1]
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", uid):
        return "https://www.youtube.com/watch?v=" + uid
    return None


class Article(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []
        self.links = set()
        self.scripts = []
        self.ld = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script" and attrs.get("type", "").lower() == "application/ld+json":
            self.ld = []
        if tag in ("article", "main", "nav", "aside", "footer", "header", "section"):
            self.stack.append(tag)
        if (("article" in self.stack or "main" in self.stack)
                and not {"nav", "aside", "footer", "header"}.intersection(self.stack)
                and tag in ("a", "iframe")):
            link = social_url(attrs.get("href" if tag == "a" else "src") or "")
            if link:
                self.links.add(link)

    def handle_data(self, data):
        if self.ld is not None:
            self.ld.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self.ld is not None:
            self.scripts.append("".join(self.ld))
            self.ld = None
        if tag in self.stack:
            while self.stack.pop() != tag:
                pass


def verified_posts(article):
    # Bind an affirmative editorial verdict to the exact reviewed post.
    for script in article.scripts:
        try:
            root = json.loads(script)
        except ValueError:
            continue
        nodes = root if isinstance(root, list) else [root]
        for node in nodes:
            if not isinstance(node, dict):
                continue
            if isinstance(node.get("@graph"), list):
                nodes.extend(node["@graph"])
            if node.get("@type") != "ClaimReview":
                continue
            rating = node.get("reviewRating")
            if not isinstance(rating, dict) or str(rating.get("alternateName", "")).strip().lower() not in {"true", "correct", "사실"}:
                continue
            claim = node.get("claimReviewed")
            item = node.get("itemReviewed")
            if not isinstance(claim, str) or not claim.strip() or not isinstance(item, dict):
                continue
            appearances = item.get("appearance", [])
            if not isinstance(appearances, list):
                appearances = [appearances]
            for entry in [item, *appearances]:
                if isinstance(entry, dict) and isinstance(entry.get("url"), str):
                    link = social_url(entry["url"])
                    if link and link.startswith("https://www.reddit.com/"):
                        yield ("Reddit", link, claim.strip())


def channel_videos(channel):
    _, data = read_page("https://www.youtube.com/feeds/videos.xml?channel_id=" + channel,
                        ["youtube.com"])
    videos = {}
    for entry in feedparser.parse(data).entries:
        link = social_url(entry.get("link", ""))
        if link and entry.get("yt_channelid") == channel and entry.get("title"):
            videos[link] = entry.title
    return videos


def collect(pages, channels, domains, fact_feeds):
    videos = {}
    entries = []

    def inspect(page):
        url, headline = page
        url, data = read_page(url, domains)
        if len(url) > 240:
            return []
        article = Article()
        article.feed(data.decode("utf-8", errors="replace"))
        records = list(verified_posts(article))
        for link in sorted(article.links & videos.keys()):
            records.append(("YouTube", link, "공개 영상 · " + videos[link]))
        return [(platform, link, fact, headline, url) for platform, link, fact in records]

    def attempt(fn, arg):
        try:
            return fn(arg)
        except (requests.RequestException, ValueError, RecursionError) as exc:
            logging.info("RSS 브리핑 조회 실패: %s", type(exc).__name__)
            return []

    def feed_pages(feed_url):
        _, data = read_page(feed_url, domains)
        cutoff = time.time() - 7 * 86400
        return [(entry.link, entry.title) for entry in feedparser.parse(data).entries
                if entry.get("link") and entry.get("title") and entry.get("published_parsed")
                and cutoff <= calendar.timegm(entry.published_parsed) <= time.time()
                and safe_page(entry.link, domains)][:10]

    with ThreadPoolExecutor(max_workers=4) as pool:
        for result in pool.map(lambda channel: attempt(channel_videos, channel), channels):
            videos.update(result)
        articles = dict(pages)
        for result in pool.map(lambda url: attempt(feed_pages, url), fact_feeds):
            articles.update(result)
        for result in pool.map(lambda page: attempt(inspect, page), articles.items()):
            entries.extend(result)
    return entries
