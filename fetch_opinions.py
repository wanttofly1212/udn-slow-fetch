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
        "feed_url": "https://aswathdamodaran.blogspot.com/feeds/posts/default?alt=rss",
        "link_re": r"aswathdamodaran\.blogspot\.com/\d{4}/\d{2}/[^\"'?#]+",
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
    raw = re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", " ", raw or "", flags=re.S | re.I)
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
        arts = re.findall(r"<article\b[^>]*>(.*?)</article>", raw or "", re.S | re.I)
        if arts:
            body = max(arts, key=len)
    if not body:
        body = raw or ""
    pub = pub or meta_of(raw, "article:published_time") or meta_of(raw, "date")
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
        if title and link:
            out.append({"title": title, "link": link, "pub": pub})
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
    pub = parse_time(pub) or parse_time(fallback_pub) or NOW
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
        "title_zh": title,
        "summary_en": summary[:600],
        "summary_zh": summary[:600],
        "body": body,
        "body_excerpt": (body[:350] + ("…" if len(body) > 350 else "")) if body else "",
        "evidence": evidence_of(body),
        "content_chars": len(body),
        "full_text_available": bool(body),
        "headline_only": len(body) < 400,
        "url": url,
        "published_at": pub,
        "tags": [],
    }


def source_items(cfg):
    out = []
    if cfg.get("feed_url"):
        status, raw, _ = fetch(cfg["feed_url"])
        if status != 200:
            return out, status, 0
        entries = feed_entries(raw)
        for e in entries[:cfg.get("max_items", 2)]:
            if not re.search(cfg["link_re"], e["link"], re.I):
                continue
            st, page, _ = fetch(e["link"])
            if st != 200:
                continue
            item = make_item(cfg, e["link"], e["title"], page, e["pub"])
            if item:
                out.append(item)
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
    if OUT.exists():
        try:
            old = json.loads(OUT.read_text(encoding="utf-8"))
            if old.get("items") == items:
                print("UNCHANGED %s items=%d" % (OUT, len(items)), flush=True)
                return
        except Exception:
            pass
    OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = {"generated_at": NOW, "sources": report, "items": items}
    tmp = OUT.with_name(OUT.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, OUT)
    print("WROTE %s items=%d" % (OUT, len(items)), flush=True)


if __name__ == "__main__":
    main()
