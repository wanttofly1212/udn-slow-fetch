# -*- coding: utf-8 -*-
"""Fetch a small set of China/macro opinion sources for the Foreign opinion tab."""
from __future__ import annotations

import hashlib
import html as html_lib
import json
import os
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
OUT = Path("data/opinion_latest.json")
NOW = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
NS = "{http://www.w3.org/2005/Atom}"

SOURCES = [
    {
        "name": "Brad Setser / CFR",
        "author": "Brad Setser",
        "platform": "CFR",
        "list_url": "https://www.cfr.org/blogs/follow-the-money",
        "link_re": r"cfr\.org/articles/[^\"'?#]+",
        "max_items": 2,
    },
    {
        "name": "Rhodium Group",
        "author": "Rhodium Group",
        "platform": "Research",
        "feed_url": "https://rhg.com/feed/",
        "link_re": r"rhg\.com/(?:news|research)/[^\"'?#]+",
        "require_re": r"china|chinese|beijing|renminbi|yuan|a-share|hong kong|tariff|export|trade|industrial|semiconductor|supply chain",
        "max_items": 2,
    },
    {
        "name": "Gavekal Dragonomics",
        "author": "Gavekal Dragonomics",
        "platform": "Research",
        "list_url": "https://research.gavekal.com/",
        "link_re": r"research\.gavekal\.com/teaser/[^\"'?#]+",
        "max_items": 2,
        "gap": 10,
    },
    {
        "name": "Carnegie / Michael Pettis",
        "author": "Michael Pettis",
        "platform": "Carnegie",
        "list_url": "https://carnegieendowment.org/china-financial-markets",
        "link_re": r"carnegieendowment\.org/(?:en/)?china-financial-markets/20\d{2}/\d{2}/[^\"'?#]+",
        "max_items": 2,
    },
    {
        "name": "Aswath Damodaran",
        "author": "Aswath Damodaran",
        "platform": "Blog",
        "list_url": "https://aswathdamodaran.blogspot.com/",
        "link_re": r"aswathdamodaran\.blogspot\.com/20\d{2}/\d{2}/[^\"'?#]+",
        "max_items": 2,
    },
]


def fetch(url, timeout=20, limit=1024 * 1024):
    try:
        req = Request(url, headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,zh-TW;q=0.8,zh;q=0.7",
        })
        with urlopen(req, timeout=timeout) as r:
            return int(r.status), r.read(limit).decode("utf-8", "ignore"), r.geturl()
    except HTTPError as e:
        try:
            e.read(100 * 1024)
        except Exception:
            pass
        return int(e.code), "", e.geturl()
    except (URLError, TimeoutError, OSError):
        return 0, "", url


def clean_text(raw):
    raw = re.sub(r"<(script|style|noscript|nav|header|footer|aside|form|svg|button)[^>]*>.*?</\1>",
                 " ", raw or "", flags=re.S | re.I)
    raw = re.sub(r"<[^>]+>", " ", raw)
    raw = html_lib.unescape(raw)
    return re.sub(r"\s+", " ", raw).strip()


def title_of(raw):
    m = re.search(r"<title[^>]*>(.*?)</title>", raw or "", re.S | re.I)
    return clean_text(m.group(1))[:240] if m else ""


def meta_of(raw, name):
    for pattern in (
        r'<meta[^>]+(?:property|name)=["\']%s["\'][^>]+content=["\']([^"\']*)' % re.escape(name),
        r'<meta[^>]+content=["\']([^"\']*)["\'][^>]+(?:property|name)=["\']%s["\']' % re.escape(name),
    ):
        m = re.search(pattern, raw or "", re.I)
        if m:
            return html_lib.unescape(m.group(1)).strip()
    return ""


def jsonld_objects(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from jsonld_objects(v)
    elif isinstance(node, list):
        for v in node:
            yield from jsonld_objects(v)


def jsonld(raw):
    for m in re.finditer(
        r"""<script[^>]*type\s*=\s*["']application/ld\+json["'][^>]*>(.*?)</script>""",
        raw or "", re.S | re.I,
    ):
        try:
            yield from jsonld_objects(json.loads(html_lib.unescape(m.group(1))))
        except Exception:
            continue


def article_fields(raw):
    body = ""
    pub = ""
    for obj in jsonld(raw):
        if not isinstance(obj, dict):
            continue
        b = obj.get("articleBody")
        if isinstance(b, str) and len(b) > len(body):
            body = b
        pub = pub or str(obj.get("datePublished") or obj.get("dateModified") or "")
    if not body:
        for tag in ("main", "article"):
            blocks = re.findall(r"<%s\b[^>]*>(.*?)</%s>" % (tag, tag), raw or "", re.S | re.I)
            if blocks:
                body = max(blocks, key=len)
                break
    if not body:
        m = re.search(r"""<div[^>]+role\s*=\s*["']main["'][^>]*>(.*?)</div>""", raw or "", re.S | re.I)
        if m:
            body = m.group(1)
    if not body:
        body = raw or ""
    pub = pub or meta_of(raw, "article:published_time") or meta_of(raw, "date")
    if not pub:
        m = re.search(r"""<time[^>]+datetime\s*=\s*["']([^"']+)["']""", raw or "", re.I)
        if m:
            pub = m.group(1)
    if not pub:
        for pat in (
            r"(?:Published on|Published|Date)\s*:?\s*([A-Z][a-z]{2,8}\s+\d{1,2},\s*20\d{2})",
            r"((?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+"
            r"(?:January|February|March|April|May|June|July|August|September|October|November|December)"
            r"\s+\d{1,2},\s*20\d{2})",
            r"(\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)"
            r"\s+20\d{2})",
            r"(20\d{2}-\d{2}-\d{2})",
        ):
            m = re.search(pat, raw or "", re.I)
            if m:
                pub = m.group(1)
                break
    summary = meta_of(raw, "description") or meta_of(raw, "og:description")
    return clean_text(body), summary, pub


def parse_time(s):
    s = str(s or "").strip()
    if not s:
        return ""
    try:
        dt = parsedate_to_datetime(s)
        if dt.tzinfo:
            dt = dt.astimezone(timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        pass
    m = re.search(r"(20\d{2})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})", s)
    if m:
        return "%s-%s-%s %s:%s:00" % m.groups()
    m = re.search(r"(20\d{2})-(\d{2})-(\d{2})", s)
    if m:
        return "%s-%s-%s 00:00:00" % m.groups()
    for fmt in (
        "%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%d %B %Y",
        "%A, %B %d, %Y", "%A %B %d, %Y", "%A, %d %B %Y",
    ):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d 00:00:00")
        except Exception:
            continue
    return ""


def links_of(raw, base, pattern, limit):
    out, seen = [], set()
    for href in re.findall(r"""<a\b[^>]*\bhref\s*=\s*["']([^"']+)["']""", raw or "", re.I):
        url = urljoin(base, html_lib.unescape(href))
        if not re.search(pattern, url, re.I) or url in seen:
            continue
        seen.add(url)
        out.append(url)
        if len(out) >= limit:
            break
    return out


def feed_entries(raw):
    try:
        root = ET.fromstring(raw)
    except Exception:
        return []
    entries = root.findall(".//item")
    if not entries:
        entries = root.findall(".//" + NS + "entry")
    out = []
    for e in entries:
        title = (e.findtext("title") or e.findtext(NS + "title") or "").strip()
        link = (e.findtext("link") or "").strip()
        if not link:
            le = e.find(NS + "link")
            if le is not None:
                link = (le.get("href") or "").strip()
        pub = (e.findtext("pubDate") or e.findtext(NS + "published")
               or e.findtext(NS + "updated") or "").strip()
        summary = (e.findtext("description") or e.findtext(NS + "summary")
                   or e.findtext(NS + "content") or "").strip()
        category = (e.findtext("category") or e.findtext(NS + "category") or "").strip()
        if title and link:
            out.append({"title": title, "link": link, "pub": pub,
                        "summary": summary, "category": category})
    return out


def evidence_of(text, limit=3):
    parts = re.split(r"(?<=[。！？.!?])\s+", text or "")
    out = []
    for s in parts:
        s = s.strip()
        if 20 <= len(s) <= 90:
            out.append(s)
        if len(out) >= limit:
            break
    return out


def make_item(cfg, url, title, raw, fallback_pub=""):
    body, summary, pub = article_fields(raw)
    title = clean_text(title) or title_of(raw)
    if not title:
        return None
    parsed_pub = parse_time(pub) or parse_time(fallback_pub)
    pub = parsed_pub or NOW
    summary = summary or body[:300]
    src = cfg["name"]
    return {
        "id": "op_" + hashlib.sha1((src + "|" + url + "|" + title).encode("utf-8")).hexdigest()[:16],
        "source": src,
        "author": cfg["author"],
        "platform": cfg["platform"],
        "content_type": "opinion",
        "opinion": True,
        "title_en": title,
        "title_zh": "",
        "summary_en": summary[:600],
        "summary_zh": "",
        "body": body[:1200],
        "body_excerpt": (body[:350] + ("…" if len(body) > 350 else "")) if body else "",
        "evidence": evidence_of(body),
        "content_chars": len(body),
        "full_text_available": bool(body),
        "headline_only": len(body) < 400,
        "url": url,
        "published_at": pub,
        "published_at_source": "published" if parsed_pub else "fetched",
        "tags": [],
    }


def source_items(cfg):
    out = []
    if cfg.get("feed_url"):
        status, raw, _ = fetch(cfg["feed_url"])
        if status != 200:
            return out, status, 0
        entries = feed_entries(raw)
        picked = 0
        for e in entries:
            if picked >= cfg.get("max_items", 2):
                break
            if not re.search(cfg["link_re"], e["link"], re.I):
                continue
            if cfg.get("require_re"):
                probe = " ".join([e.get("title") or "", e.get("summary") or ""])
                if not re.search(cfg["require_re"], probe, re.I):
                    continue
            st, page, _ = fetch(e["link"])
            if st != 200:
                continue
            item = make_item(cfg, e["link"], e["title"], page, e["pub"])
            if item:
                out.append(item)
                picked += 1
            time.sleep(1.2)
        return out, status, len(entries)

    status, raw, _ = fetch(cfg["list_url"])
    if status != 200:
        return out, status, 0
    links = links_of(raw, cfg["list_url"], cfg["link_re"], cfg.get("max_items", 2))
    for url in links:
        st, page, _ = fetch(url)
        if st == 200:
            item = make_item(cfg, url, title_of(page), page)
            if item:
                out.append(item)
        time.sleep(cfg.get("gap", 1.2))
    return out, status, len(links)


UDN_FEEDS = [
    {"source": "UDN 经济日报", "url": "https://money.udn.com/rssfeed/news/1001/5588/5599?ch=money",
     "section": "財經"},
    {"source": "UDN 联合新闻", "url": "https://udn.com/rssfeed/news/2/6638?ch=news",
     "section": "國際"},
]
UDN_ALLOW = ("財經", "經濟", "金融", "稅務", "貿易", "產業", "政治", "軍事", "國防",
             "國際", "兩岸", "外交", "關稅", "制裁", "出口", "半導體", "供應鏈",
             "央行", "利率", "匯率", "台海", "俄烏", "中東", "朝鮮", "能源")
UDN_DROP = ("社會", "地方", "文教", "運動", "娛樂", "生活", "健康", "旅遊", "美食",
            "藝術", "時尚", "寵物", "影視", "綜藝", "球賽", "養生")


def _udn_ok(title, summary="", section=""):
    text = " ".join([str(title or ""), str(summary or ""), str(section or "")])
    if any(x in text for x in UDN_DROP):
        return False
    return any(x in text for x in UDN_ALLOW)


def fetch_udn():
    """在 GitHub Actions 里抓 UDN；本机直连 timeout，不在本机跑。"""
    out, seen = [], set()
    report = {}
    for feed in UDN_FEEDS:
        status, raw, _ = fetch(feed["url"], timeout=25)
        entries = feed_entries(raw) if status == 200 else []
        picked = 0
        for e in entries:
            if picked >= 3:
                break
            title = clean_text(e.get("title"))
            summary = clean_text(e.get("summary"))
            url = (e.get("link") or "").strip()
            if not title or not url or url in seen:
                continue
            if not _udn_ok(title, summary, e.get("category") or feed["section"]):
                continue
            seen.add(url)
            out.append({
                "source": "UDN", "source_tier": "A",
                "title_en": title, "title_zh": "",
                "summary_en": summary[:600], "summary_zh": "",
                "url": url, "section": e.get("category") or feed["section"],
                "published_at": parse_time(e.get("pub") or "") or NOW,
                "published_at_source": "published" if parse_time(e.get("pub") or "") else "fetched",
                "headline_only": True, "content_type": "news", "opinion": False,
                "discovery": feed["source"],
            })
            picked += 1
        report[feed["source"]] = {"status": status, "found": len(entries), "items": picked}
        print("%-28s status=%s found=%s items=%s" % (feed["source"], status, len(entries), picked), flush=True)
        time.sleep(1.2)
    return out, report


def fetch_scmp():
    """SCMP：GitHub Actions 侧抓文章页，正文走 JSON-LD/article；本机直连 timeout。"""
    out, report = [], {}
    status, raw, _ = fetch("https://www.scmp.com/", timeout=25)
    if status != 200:
        report["SCMP"] = {"status": status, "found": 0, "items": 0}
        return out, report
    links = links_of(raw, "https://www.scmp.com/", r"scmp\.com/(?:[^\"']*/)?article/\d+", 3)
    for url in links:
        st, page, _ = fetch(url, timeout=25)
        if st != 200:
            continue
        title = title_of(page)
        body, summary, pub = article_fields(page)
        body = clean_text(body)
        summary = clean_text(summary or "")
        if not title or len(body) < 200:
            continue
        out.append({
            "source": "SCMP", "source_tier": "A",
            "title_en": title, "title_zh": "",
            "summary_en": (summary or body[:600])[:600], "summary_zh": "",
            "body": body[:1200], "body_excerpt": body[:350],
            "url": url, "section": "",
            "published_at": parse_time(pub) or NOW,
            "published_at_source": "published" if parse_time(pub) else "fetched",
            "headline_only": len(body) < 400, "content_type": "news", "opinion": False,
        })
        time.sleep(10)
    report["SCMP"] = {"status": status, "found": len(links), "items": len(out)}
    print("%-28s status=%s found=%s items=%s" % ("SCMP", status, len(links), len(out)), flush=True)
    return out, report


REMOTE_RSS = [
    ("Bloomberg Markets", "https://feeds.bloomberg.com/markets/news.rss"),
    ("Reuters Business", "https://feeds.reuters.com/reuters/businessNews"),
    ("Yahoo Finance", "https://finance.yahoo.com/news/rssindex"),
]


def fetch_remote_rss():
    """公开 RSS 标题层；正文受限的源只入标题，不伪造全文。"""
    out, report = [], {}
    for source, url in REMOTE_RSS:
        status, raw, _ = fetch(url, timeout=20)
        entries = feed_entries(raw) if status == 200 else []
        picked = 0
        for e in entries:
            if picked >= 2:
                break
            title = clean_text(e.get("title"))
            link = (e.get("link") or "").strip()
            if len(title) < 16 or not link:
                continue
            out.append({
                "source": source, "source_tier": "B",
                "title_en": title, "title_zh": "",
                "summary_en": clean_text(e.get("summary"))[:600], "summary_zh": "",
                "url": link, "section": "",
                "published_at": parse_time(e.get("pub") or "") or NOW,
                "published_at_source": "published" if parse_time(e.get("pub") or "") else "fetched",
                "headline_only": True, "content_type": "news", "opinion": False,
            })
            picked += 1
        report[source] = {"status": status, "found": len(entries), "items": picked}
        print("%-28s status=%s found=%s items=%s" % (source, status, len(entries), picked), flush=True)
    return out, report


def main():
    items = []
    report = {}
    seen = set()
    for cfg in SOURCES:
        got, status, found = source_items(cfg)
        items.extend(got)
        report[cfg["name"]] = {"status": status, "found": found, "items": len(got)}
        print("%-28s status=%s found=%s items=%s" % (cfg["name"], status, found, len(got)), flush=True)
        for x in got:
            key = x["url"].split("?")[0]
            if key in seen:
                items.remove(x)
            else:
                seen.add(key)
    items.sort(key=lambda x: x.get("published_at") or "", reverse=True)
    news_items, news_report = fetch_udn()
    report.update(news_report)
    scmp_items, scmp_report = fetch_scmp()
    news_items.extend(scmp_items)
    report.update(scmp_report)
    rss_items, rss_report = fetch_remote_rss()
    news_items.extend(rss_items)
    report.update(rss_report)
    if OUT.exists():
        try:
            old = json.loads(OUT.read_text(encoding="utf-8"))
            if old.get("items") == items and old.get("news_items") == news_items:
                print("UNCHANGED %s items=%d news=%d" % (OUT, len(items), len(news_items)), flush=True)
                return
        except Exception:
            pass
    OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = {"generated_at": NOW, "sources": report, "items": items,
               "news_items": news_items}
    tmp = OUT.with_name(OUT.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, OUT)
    print("WROTE %s items=%d" % (OUT, len(items)), flush=True)


if __name__ == "__main__":
    main()
