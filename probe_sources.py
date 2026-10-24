# -*- coding: utf-8 -*-
"""Probe source list/article access, body size, robots and observed rate limits."""
from __future__ import annotations

import html as html_lib
import json
import os
import platform
import re
import sys
import time
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
MAX_ARTICLES = 3
READ_LIMIT = 1024 * 1024
TIMEOUT = 18

SOURCES = [
    {
        "name": "UDN money",
        "list_url": "https://money.udn.com/money/index",
        "article_re": r"money\.udn\.com/money/story/\d+",
    },
    {
        "name": "UDN news",
        "list_url": "https://udn.com/news/index",
        "article_re": r"udn\.com/news/story/\d+",
    },
    {
        "name": "SCMP",
        "list_url": "https://www.scmp.com/",
        "article_re": r"scmp\.com/(?:[^\"']*/)?article/\d+",
    },
    {
        "name": "Caixin Global",
        "list_url": "https://www.caixinglobal.com/",
        "article_re": r"caixinglobal\.com/\d{4}-\d{2}-\d{2}/[^\"'?#]+",
    },
    {
        "name": "BNN Bloomberg",
        "list_url": "https://www.bnnbloomberg.ca/",
        "article_re": r"bnnbloomberg\.ca/(?:business|news|markets|investing)/\d{4}/\d{2}/\d{2}/[^\"'?#]+",
    },
    {
        "name": "Yahoo Finance",
        "list_url": "https://finance.yahoo.com/",
        "article_re": r"finance\.yahoo\.com/news/[^\"'?#]+",
    },
    {
        "name": "Reuters",
        "list_url": "https://www.reuters.com/",
        "article_re": r"reuters\.com/[^\"'?#]+",
    },
    {
        "name": "Bloomberg",
        "list_url": "https://www.bloomberg.com/",
        "article_re": r"bloomberg\.com/news/[^\"'?#]+",
    },
    {
        "name": "Investing.com",
        "list_url": "https://www.investing.com/",
        "article_re": r"investing\.com/news/[^\"'?#]+",
    },
    {
        "name": "CNBC RSS",
        "feed_url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10000664",
        "article_re": r"cnbc\.com/\d{4}/\d{2}/\d{2}/[^\"'?#]+",
    },
    {
        "name": "MarketWatch RSS",
        "feed_url": "https://feeds.content.dowjones.io/public/rss/mw_topstories",
        "article_re": r"marketwatch\.com/story/[^\"'?#]+",
    },
    {
        "name": "Google News RSS",
        "feed_url": "https://news.google.com/rss/search?q=china+offshore+trust&hl=en-US&gl=US&ceid=US:en",
    },
    {
        "name": "GDELT",
        "api_url": "https://api.gdeltproject.org/api/v2/doc/doc?query=china%20offshore%20trust&mode=ArtList&maxrecords=3&format=json&sort=datedesc",
    },
    {
        "name": "FT",
        "list_url": "https://www.ft.com/",
        "article_re": r"ft\.com/content/[0-9a-f-]+",
        "max_articles": 2,
    },
    {
        "name": "WSJ",
        "list_url": "https://www.wsj.com/",
        "article_re": r"wsj\.com/articles/[^\"'?#]+",
        "max_articles": 2,
    },
    {
        "name": "Barron's",
        "list_url": "https://www.barrons.com/",
        "article_re": r"barrons\.com/articles/[^\"'?#]+",
        "max_articles": 2,
    },
    {
        "name": "The Economist",
        "list_url": "https://www.economist.com/",
        "article_re": r"economist\.com/[^\"'?#]+/\d{4}/\d{2}/\d{2}/[^\"'?#]+",
        "max_articles": 2,
    },
    {
        "name": "Nikkei Asia",
        "list_url": "https://asia.nikkei.com/",
        "article_re": r"asia\.nikkei\.com/[^\"'?#]+",
        "max_articles": 2,
    },
    {
        "name": "AP News",
        "list_url": "https://apnews.com/",
        "article_re": r"apnews\.com/article/[^\"'?#]+",
        "max_articles": 2,
    },
    {
        "name": "Bloomberg RSS",
        "feed_url": "https://feeds.bloomberg.com/markets/news.rss",
        "article_re": r"bloomberg\.com/news/articles/[^\"'?#]+",
        "max_articles": 2,
    },
    {
        "name": "Reuters RSS",
        "feed_url": "https://feeds.reuters.com/reuters/businessNews",
        "article_re": r"reuters\.com/[^\"'?#]+",
        "max_articles": 2,
    },
    {
        "name": "Opinion: Michael Pettis",
        "feed_url": "https://michaelpettis858496.substack.com/feed",
        "article_re": r"michaelpettis858496\.substack\.com/p/[^\"'?#]+",
        "max_articles": 2,
    },
    {
        "name": "Opinion: Adam Tooze",
        "feed_url": "https://adamtooze.substack.com/feed",
        "article_re": r"adamtooze\.substack\.com/p/[^\"'?#]+",
        "max_articles": 2,
    },
    {
        "name": "Opinion: Aswath Damodaran",
        "feed_url": "https://aswathdamodaran.substack.com/feed",
        "article_re": r"aswathdamodaran\.substack\.com/p/[^\"'?#]+",
        "max_articles": 2,
    },
    {
        "name": "Opinion: Brad Setser",
        "list_url": "https://www.cfr.org/blogs/follow-the-money",
        "article_re": r"cfr\.org/articles/[^\"'?#]+",
        "max_articles": 2,
    },
    {
        "name": "Opinion: Rhodium",
        "feed_url": "https://rhg.com/feed/",
        "article_re": r"rhg\.com/[^\"'?#]+",
        "max_articles": 2,
    },
    {
        "name": "Opinion: MacroPolo",
        "feed_url": "https://macropolo.org/feed/",
        "article_re": r"macropolo\.org/[^\"'?#]+",
        "max_articles": 2,
    },
    {
        "name": "Opinion: Gavekal",
        "list_url": "https://research.gavekal.com/",
        "article_re": r"research\.gavekal\.com/[^\"'?#]+",
        "max_articles": 2,
    },
    {
        "name": "Opinion: Noahpinion",
        "feed_url": "https://noahpinion.blog/feed",
        "article_re": r"noahpinion\.blog/p/[^\"'?#]+",
        "max_articles": 2,
    },
    {
        "name": "Opinion: Doomberg",
        "feed_url": "https://doomberg.substack.com/feed",
        "article_re": r"doomberg\.substack\.com/p/[^\"'?#]+",
        "max_articles": 2,
    },
    {
        "name": "Opinion: Net Interest",
        "feed_url": "https://netinterest.co/feed",
        "article_re": r"netinterest\.co/p/[^\"'?#]+",
        "max_articles": 2,
    },
    {
        "name": "Opinion: Carnegie Pettis",
        "list_url": "https://carnegieendowment.org/china-financial-markets",
        "article_re": r"carnegieendowment\.org/(?!china-financial-markets)[^\"'?#]+",
        "max_articles": 2,
    },
    {
        "name": "Opinion: Aswath blog",
        "feed_url": "https://aswathdamodaran.blogspot.com/feeds/posts/default",
        "article_re": r"aswathdamodaran\.blogspot\.com/\d{4}/\d{2}/[^\"'?#]+",
        "max_articles": 2,
    },
    {
        "name": "Opinion: CFR RSS",
        "feed_url": "https://www.cfr.org/rss.xml",
        "article_re": r"cfr\.org/articles/[^\"'?#]+",
        "max_articles": 2,
    },
    {
        "name": "Opinion: PIIE",
        "feed_url": "https://www.piie.com/rss.xml",
        "article_re": r"piie\.com/publications/[^\"'?#]+",
        "max_articles": 2,
    },
]

PAYWALL_MARKERS = [
    "subscribe to continue",
    "already a subscriber",
    "sign in to continue",
    "create a free account to continue",
    "this article is for subscribers",
    "premium content",
    "订阅",
    "付费",
    "登录后",
]


def fetch(url, timeout=TIMEOUT, limit=READ_LIMIT):
    started = time.time()
    status = 0
    final_url = url
    content_type = ""
    raw = b""
    error = ""
    retry_after = ""
    try:
        req = Request(
            url,
            headers={
                "User-Agent": UA,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9,zh-TW;q=0.8,zh;q=0.7",
            },
        )
        with urlopen(req, timeout=timeout) as resp:
            status = int(resp.status)
            final_url = resp.geturl()
            content_type = resp.headers.get("content-type", "")
            retry_after = resp.headers.get("retry-after", "")
            raw = resp.read(limit)
    except HTTPError as e:
        status = int(e.code)
        final_url = e.geturl()
        content_type = e.headers.get("content-type", "") if e.headers else ""
        retry_after = e.headers.get("retry-after", "") if e.headers else ""
        try:
            raw = e.read(min(limit, 200 * 1024))
        except Exception:
            raw = b""
        error = "HTTP %s" % e.code
    except (URLError, TimeoutError, OSError) as e:
        error = "%s: %s" % (type(e).__name__, str(e)[:240])
    except Exception as e:
        error = "%s: %s" % (type(e).__name__, str(e)[:240])

    text = raw.decode("utf-8", "ignore")
    return {
        "status": status,
        "ok": 200 <= status < 300,
        "final_url": final_url,
        "content_type": content_type[:120],
        "bytes": len(raw),
        "elapsed_ms": int((time.time() - started) * 1000),
        "error": error,
        "retry_after": retry_after,
        "text": text,
    }


def title_of(text, content_type=""):
    if "html" not in (content_type or "").lower() and "xml" not in (content_type or "").lower():
        return ""
    m = re.search(r"<title[^>]*>(.*?)</title>", text or "", re.S | re.I)
    if not m:
        return ""
    return re.sub(r"\s+", " ", html_lib.unescape(re.sub(r"<[^>]+>", "", m.group(1)))).strip()[:160]


def body_text(raw_text):
    text = re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", " ", raw_text or "", flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_lib.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _jsonld_objects(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _jsonld_objects(v)
    elif isinstance(node, list):
        for v in node:
            yield from _jsonld_objects(v)


def extract_main_html(raw_html):
    """优先 JSON-LD articleBody，其次 <article>，最后整页。"""
    for m in re.finditer(
        r"""<script[^>]*type\s*=\s*["']application/ld\+json["'][^>]*>(.*?)</script>""",
        raw_html or "",
        re.S | re.I,
    ):
        try:
            data = json.loads(html_lib.unescape(m.group(1)))
        except Exception:
            continue
        for obj in _jsonld_objects(data):
            body = obj.get("articleBody") if isinstance(obj, dict) else None
            if isinstance(body, str) and len(body.strip()) >= 200:
                return body, "jsonld"
    arts = re.findall(r"<article\b[^>]*>(.*?)</article>", raw_html or "", re.S | re.I)
    if arts:
        return max(arts, key=len), "article_tag"
    m = re.search(
        r"""<div[^>]+class\s*=\s*["'][^"']*(?:article|story)[^"']*(?:body|content)[^"']*["'][^>]*>(.*?)</div>""",
        raw_html or "",
        re.S | re.I,
    )
    if m:
        return m.group(1), "article_div"
    return raw_html or "", "full_page"


def links_of(raw_text, base, pattern, limit=MAX_ARTICLES):
    out, seen = [], set()
    for href in re.findall(r"""<a\b[^>]*\bhref\s*=\s*["']([^"']+)["']""", raw_text or "", re.I):
        url = urljoin(base, html_lib.unescape(href))
        if not re.search(pattern, url, re.I):
            continue
        if url in seen:
            continue
        seen.add(url)
        out.append(url)
        if len(out) >= limit:
            break
    return out


def feed_items(raw_text, limit=MAX_ARTICLES):
    try:
        root = ET.fromstring(raw_text)
    except Exception:
        return []
    out = []
    for it in root.findall(".//item")[:limit]:
        out.append({
            "title": (it.findtext("title") or "").strip(),
            "link": (it.findtext("link") or "").strip(),
            "pub": (it.findtext("pubDate") or "").strip(),
        })
    return out


def robots_of(url):
    u = urlparse(url)
    robots_url = "%s://%s/robots.txt" % (u.scheme, u.netloc)
    r = fetch(robots_url, timeout=10, limit=200 * 1024)
    crawl = ""
    disallow = 0
    if r["ok"]:
        m = re.search(r"(?im)^\s*crawl-delay\s*:\s*([0-9.]+)", r["text"])
        if m:
            crawl = m.group(1)
        disallow = len(re.findall(r"(?im)^\s*disallow\s*:", r["text"]))
    return {
        "url": robots_url,
        "status": r["status"],
        "crawl_delay": crawl,
        "disallow_rules": disallow,
    }


def rate_probe(url, attempts=3, gap=2.0):
    out = []
    for i in range(attempts):
        r = fetch(url, timeout=12)
        out.append({
            "status": r["status"],
            "elapsed_ms": r["elapsed_ms"],
            "retry_after": r["retry_after"],
            "error": r["error"],
        })
        if i + 1 < attempts:
            time.sleep(gap)
    return out


def article_probe(url, pattern):
    started = time.time()
    r = fetch(url, timeout=TIMEOUT)
    main_html, extract_method = extract_main_html(r["text"])
    text = body_text(main_html)
    low = text.lower()
    hits = [x for x in PAYWALL_MARKERS if x in low]
    return {
        "url": url,
        "status": r["status"],
        "bytes": r["bytes"],
        "text_chars": len(text),
        "extract": extract_method,
        "title": title_of(r["text"], r["content_type"]) or "",
        "paywall_hits": hits,
        "ok": bool(r["ok"] and len(text) >= 400 and not hits),
        "elapsed_ms": r["elapsed_ms"],
        "error": r["error"],
    }


def main():
    results = []
    for cfg in SOURCES:
        name = cfg["name"]
        print("\n== %s ==" % name, flush=True)
        row = {
            "source": name,
            "list": None,
            "robots": None,
            "feed_items": 0,
            "articles": [],
            "rate": [],
            "suggested_interval": "",
            "note": "",
        }

        if cfg.get("api_url"):
            r = fetch(cfg["api_url"], timeout=25)
            row["list"] = {k: r[k] for k in ("status", "bytes", "elapsed_ms", "error", "retry_after")}
            try:
                row["feed_items"] = len((json.loads(r["text"]) or {}).get("articles") or [])
            except Exception:
                row["feed_items"] = 0
            row["suggested_interval"] = ">= 15 分钟（实测单次约 15 秒）"
        elif cfg.get("feed_url"):
            r = fetch(cfg["feed_url"], timeout=20)
            row["list"] = {k: r[k] for k in ("status", "bytes", "elapsed_ms", "error", "retry_after")}
            items = feed_items(r["text"], limit=cfg.get("max_articles", MAX_ARTICLES))
            row["feed_items"] = len(items)
            row["robots"] = robots_of(cfg["feed_url"])
            row["rate"] = rate_probe(cfg["feed_url"], attempts=2, gap=2.0)
            for it in items:
                if it["link"] and re.search(cfg.get("article_re", ""), it["link"], re.I):
                    row["articles"].append(article_probe(it["link"], cfg.get("article_re", "")))
                time.sleep(1.2)
            row["suggested_interval"] = ">= 15 分钟（RSS 本身更新不频繁）"
        else:
            row["robots"] = robots_of(cfg["list_url"])
            r = fetch(cfg["list_url"], timeout=TIMEOUT)
            row["list"] = {k: r[k] for k in ("status", "bytes", "elapsed_ms", "error", "retry_after")}
            row["rate"] = rate_probe(cfg["list_url"], attempts=3, gap=2.0)
            urls = links_of(r["text"], cfg["list_url"], cfg["article_re"], limit=cfg.get("max_articles", MAX_ARTICLES))
            for url in urls:
                row["articles"].append(article_probe(url, cfg["article_re"]))
                time.sleep(1.2)
            if any(x.get("status") == 429 for x in row["rate"]):
                row["suggested_interval"] = ">= 60 分钟（已见 429）"
            elif row["robots"].get("crawl_delay"):
                row["suggested_interval"] = ">= %s 秒（robots Crawl-delay）" % row["robots"]["crawl_delay"]
            else:
                row["suggested_interval"] = ">= 15 分钟（Actions 批处理，避免压力）"

        ok_articles = sum(1 for x in row["articles"] if x.get("ok"))
        print(
            "list=%s articles=%d/%d interval=%s"
            % (
                (row["list"] or {}).get("status", "-"),
                ok_articles,
                len(row["articles"]),
                row["suggested_interval"],
            ),
            flush=True,
        )
        for a in row["articles"]:
            print(
                "  article %s chars=%s paywall=%s"
                % (a["status"] or "-", a["text_chars"], ",".join(a["paywall_hits"]) or "-"),
                flush=True,
            )
        results.append(row)

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runner": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "github_actions": os.environ.get("GITHUB_ACTIONS") == "true",
        },
        "results": results,
    }
    with open("probe_results.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("## Source capability probe\n\n")
            f.write("| source | list | articles | text chars | paywall | interval |\n")
            f.write("|---|---:|---:|---|---|---|\n")
            for r in results:
                chars = [a["text_chars"] for a in r["articles"]]
                pay = sum(1 for a in r["articles"] if a["paywall_hits"])
                f.write(
                    "| %s | %s | %d/%d | %s | %s | %s |\n"
                    % (
                        r["source"],
                        (r["list"] or {}).get("status", "-"),
                        sum(1 for a in r["articles"] if a["ok"]),
                        len(r["articles"]),
                        ("%d-%d" % (min(chars), max(chars))) if chars else "-",
                        pay,
                        r["suggested_interval"],
                    )
                )


if __name__ == "__main__":
    main()
