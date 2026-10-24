# -*- coding: utf-8 -*-
"""Probe source reachability from GitHub Actions runners."""
from __future__ import annotations

import json
import os
import platform
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

TARGETS = [
    ("UDN money", "https://money.udn.com/money/index"),
    ("UDN news", "https://udn.com/news/index"),
    ("Reuters", "https://www.reuters.com/"),
    ("Bloomberg", "https://www.bloomberg.com/"),
    ("SCMP", "https://www.scmp.com/"),
    ("Caixin Global", "https://www.caixinglobal.com/"),
    ("BNN Bloomberg", "https://www.bnnbloomberg.ca/"),
    ("Google News RSS", "https://news.google.com/rss/search?q=china+offshore+trust&hl=en-US&gl=US&ceid=US:en"),
    ("GDELT", "https://api.gdeltproject.org/api/v2/doc/doc?query=china%20offshore%20trust&mode=ArtList&maxrecords=3&format=json&sort=datedesc"),
    ("CNBC RSS", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10000664"),
    ("MarketWatch RSS", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
    ("Yahoo Finance", "https://finance.yahoo.com/"),
    ("Investing.com", "https://www.investing.com/"),
]


def _title(text: str, content_type: str) -> str:
    if "html" not in (content_type or "").lower() and "xml" not in (content_type or "").lower():
        return ""
    m = re.search(r"<title[^>]*>(.*?)</title>", text or "", re.S | re.I)
    if not m:
        return ""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", m.group(1))).strip()[:160]


def probe(item):
    name, url = item
    started = time.time()
    status = 0
    final_url = url
    content_type = ""
    raw = b""
    error = ""
    try:
        req = Request(
            url,
            headers={
                "User-Agent": UA,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9,zh-TW;q=0.8,zh;q=0.7",
            },
        )
        with urlopen(req, timeout=25) as resp:
            status = int(resp.status)
            final_url = resp.geturl()
            content_type = resp.headers.get("content-type", "")
            raw = resp.read(1024 * 1024)
    except HTTPError as e:
        status = int(e.code)
        final_url = e.geturl()
        content_type = e.headers.get("content-type", "") if e.headers else ""
        try:
            raw = e.read(200 * 1024)
        except Exception:
            raw = b""
        error = "HTTP %s" % e.code
    except (URLError, TimeoutError, OSError) as e:
        error = "%s: %s" % (type(e).__name__, str(e)[:240])
    except Exception as e:
        error = "%s: %s" % (type(e).__name__, str(e)[:240])

    text = raw.decode("utf-8", "ignore")
    return {
        "source": name,
        "url": url,
        "status": status,
        "ok": 200 <= status < 300,
        "bytes": len(raw),
        "content_type": content_type[:120],
        "final_url": final_url,
        "title": _title(text, content_type),
        "elapsed_ms": int((time.time() - started) * 1000),
        "error": error,
    }


def main():
    results = []
    with ThreadPoolExecutor(max_workers=5) as ex:
        futures = [ex.submit(probe, t) for t in TARGETS]
        for f in as_completed(futures):
            results.append(f.result())
    results.sort(key=lambda x: [t[0] for t in TARGETS].index(x["source"]))

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

    print("%-18s %6s %9s %7s  %s" % ("source", "status", "bytes", "ms", "title"))
    print("-" * 92)
    for r in results:
        print(
            "%-18s %6s %9s %7s  %s"
            % (r["source"], r["status"] or "-", r["bytes"], r["elapsed_ms"], r["title"][:48])
        )
        if r["error"]:
            print("  error: %s" % r["error"])

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("## Source probe\n\n")
            f.write("| source | status | bytes | ms | title |\n")
            f.write("|---|---:|---:|---:|---|\n")
            for r in results:
                title = (r["title"] or "").replace("|", "\\|")
                f.write(
                    "| %s | %s | %s | %s | %s |\n"
                    % (r["source"], r["status"] or "-", r["bytes"], r["elapsed_ms"], title[:100])
                )


if __name__ == "__main__":
    main()
