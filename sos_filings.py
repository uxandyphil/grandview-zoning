"""
Ohio Secretary of State business filings.

First step: find where the Secretary of State publishes its business filing reports.
Visits the business pages, keeps every link that looks like a report or a download,
and writes them to docs/sos-sources.json so the import can be built on the real files.
Runs from scrape.py.
"""

import json
import re
from pathlib import Path
from urllib.parse import urljoin

import requests

SOURCES_OUT = Path("docs/sos-sources.json")
PAGES = [
    "https://www.ohiosos.gov/businesses/business-reports/",
    "https://www.ohiosos.gov/businesses/",
    "https://www.ohiosos.gov/media-center/",
    "https://businesssearch.ohiosos.gov/",
    "https://publicfiles.ohiosos.gov/free/",
]
KEEP = re.compile(r"report|filing|new.?business|download|\.(xlsx?|csv|zip|txt|pdf)\b|business.?search|data", re.I)
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"}


def fetch(url):
    """(status, final url, html). Falls back to a real browser if the site turns away plain requests."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=60)
        if r.status_code < 400:
            return r.status_code, r.url, r.text
        status = f"{r.status_code} ({r.headers.get('server', '?')}): " + re.sub(r"<[^>]+>|\s+", " ", r.text)[:300]
    except requests.RequestException as err:
        status = f"error: {err}"[:120]
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch()
            page = b.new_page(user_agent=HEADERS["User-Agent"])
            resp = page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(15000)   # give a bot check time to finish
            html, final = page.content(), page.url
            if not links(final, html):
                status = f"{status}; browser {resp.status if resp else '?'}: " + re.sub(r"<[^>]+>|\s+", " ", html)[:300]
            b.close()
            return (resp.status if resp else status), final, html
    except Exception as err:
        return f"{status}; browser: {err}"[:200], url, ""


def links(base, html):
    out = []
    for href, text in re.findall(r'<a\b[^>]*href="([^"#]+)"[^>]*>(.*?)</a>', html, re.I | re.S):
        text = re.sub(r"<[^>]+>|\s+", " ", text).strip()[:120]
        url = urljoin(base, href)
        if KEEP.search(url) or KEEP.search(text):
            out.append({"text": text, "url": url})
    return list({l["url"]: l for l in out}.values())


def run(state, now):
    report, seen = {"updated": now, "pages": {}}, set()
    todo = list(PAGES)
    while todo and len(seen) < 25:
        url = todo.pop(0)
        if url in seen:
            continue
        seen.add(url)
        status, final, html = fetch(url)
        title = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
        found = links(final, html)
        report["pages"][url] = {"status": status, "final_url": final, "title": title and title[1].strip()[:120], "links": found}
        print(f"SOS: {url} -> {status}, {len(found)} links")
        if url == PAGES[0]:   # one level into the report pages
            todo += [l["url"] for l in found if "ohiosos.gov" in l["url"] and re.search(r"report|filing", l["url"], re.I)
                     and not re.search(r"\.(xlsx?|csv|zip|pdf|txt)$", l["url"], re.I)][:15]
    SOURCES_OUT.write_text(json.dumps(report, indent=1))
    print(f"SOS: wrote {SOURCES_OUT} ({len(report['pages'])} pages)")
