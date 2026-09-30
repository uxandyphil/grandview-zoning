"""
Temporary: checks what a few public sites return to the GitHub runner (City Council agendas,
the county recorder, sheriff sales, and court foreclosure filings) and writes docs/probe.json,
so the importers can be built on what's actually there. Removed once they exist.
"""

import json
import re
from pathlib import Path
from urllib.parse import urljoin

import requests

OUT = Path("docs/probe.json")
PAGES = [
    "https://www.grandviewheights.gov/calendar.aspx?CID=26,14,27,34",
    "https://www.grandviewheights.gov/AgendaCenter",
    "https://www.grandviewheights.gov/DocumentCenter",
    "https://franklin.sheriffsaleauction.ohio.gov/index.cfm?zaction=USER&zmethod=CALENDAR",
    "https://franklin.sheriffsaleauction.ohio.gov/index.cfm?zaction=AUCTION&zmethod=PREVIEW&AuctionDate=10/02/2026",
    "https://clerknewfiling.franklincountyohio.gov/",
    "https://clerknewfiling.franklincountyohio.gov/api/submissions",
    "https://www.franklincountyohio.gov/Agency-Directory/Recorder/Real-Estate/Public-Records-Search",
]
BROWSER_ALWAYS = ("DocumentCenter", "PREVIEW", "CALENDAR", "clerknewfiling.franklincountyohio.gov/")
KEEP = re.compile(r"agenda|council|DocumentCenter|Calendar|EID=|minutes|sale|sheriff|foreclos|filing|record|search|case|auction|calendar|list|"
                  r"ViewFile|ADID|AMID|\.(pdf|xlsx?|csv|txt)\b", re.I)
FOLLOW = re.compile(r"cloud|search records|recordsearch|EID=|new.*filings|unapproved|search records|official records|sheriff sale|real estate sale|"
                    r"auction calendar|preview|foreclos", re.I)
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"


def text_of(html):
    return re.sub(r"\s+", " ", re.sub(r"<script.*?</script>|<style.*?</style>|<[^>]+>", " ", html, flags=re.S | re.I)).strip()


def links(base, html):
    out = {}
    for href, t in re.findall(r'<a\b[^>]*href="([^"#]+)"[^>]*>(.*?)</a>', html, re.I | re.S):
        t = text_of(t)[:100]
        u = urljoin(base, href.replace("&amp;", "&"))
        if KEEP.search(u) or KEEP.search(t):
            out[u] = t
    return [{"text": t, "url": u} for u, t in out.items()][:120]


def fetch(url):
    note = ""
    if url.endswith(BROWSER_ALWAYS) or any(b in url for b in BROWSER_ALWAYS[:3]):
        note = "browser first"
    else:
      try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=45)
        if r.status_code < 400 and len(r.text) > 300:
            return {"via": "requests", "status": r.status_code, "final": r.url, "html": r.text,
                    "type": r.headers.get("content-type", "")}
        note = f"requests {r.status_code} ({r.headers.get('server', '?')}) {r.text[:200]}"
      except requests.RequestException as err:
        note = f"requests error {err}"[:150]
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch()
            page = b.new_page(user_agent=UA)
            resp = page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(10000)
            body = page.evaluate("document.body ? document.body.innerText : ''")
            out = {"via": "browser", "status": resp.status if resp else None, "final": page.url, "html": page.content(),
                   "note": note, "body": body[:5000]}
            b.close()
            return out
    except Exception as err:
        return {"via": "none", "status": None, "final": url, "html": "", "note": f"{note}; browser {err}"[:300]}


def run(state, now):
    report, seen, todo = {"updated": now, "pages": {}}, set(), list(PAGES)
    while todo and len(seen) < 22:
        url = todo.pop(0)
        if url in seen:
            continue
        seen.add(url)
        r = fetch(url)
        html = r.pop("html")
        title = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
        found = links(r["final"], html)
        report["pages"][url] = {**r, "title": title and text_of(title[1])[:120], "text": text_of(html)[:3000], "links": found}
        print(f"Probe: {url} -> {r['via']} {r['status']}, {len(found)} links")
        if url in PAGES:
            todo += [l["url"] for l in found if FOLLOW.search(l["text"] + " " + l["url"]) and l["url"] not in seen][:3]
    OUT.write_text(json.dumps(report, indent=1))
