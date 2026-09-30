"""
Temporary: records the data requests a few public pages make (City Council's CivicClerk portal, the
recorder's PublicSearch, and one foreclosure filing's documents), and writes docs/probe.json, so the
importers can call the right addresses. Removed once they work.
"""

import json
from pathlib import Path

import requests

OUT = Path("docs/probe.json")
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
PAGES = [
    "https://grandviewheightsoh.portal.civicclerk.com/",
    "https://franklin.oh.publicsearch.us/results?department=RP&limit=50&offset=0&recordedDateRange=20260901%2C20260929"
    "&searchOcrText=false&searchType=quickSearch&searchValue=GRANDVIEW%20HEIGHTS",
]


def watch(page_url, b):
    """Every XHR/fetch the page makes, with a sample of each JSON response."""
    page, calls = b.new_page(user_agent=UA), []
    def on_response(r):
        if r.request.resource_type in ("xhr", "fetch"):
            entry = {"url": r.url[:400], "status": r.status, "type": r.headers.get("content-type", "")}
            try:
                if "json" in entry["type"]:
                    entry["sample"] = r.text()[:1500]
            except Exception:
                pass
            calls.append(entry)
    page.on("response", on_response)
    try:
        page.goto(page_url, wait_until="networkidle", timeout=60000)
    except Exception as err:
        calls.append({"error": str(err)[:200]})
    page.wait_for_timeout(5000)
    body = page.evaluate("document.body ? document.body.innerText : ''")[:3000]
    page.close()
    return {"calls": calls[:60], "body": body}


def run(state, now):
    report = {"updated": now, "pages": {}}
    # one foreclosure filing's documents, by plain request and by browser
    try:
        subs = requests.get("https://clerknewfiling.franklincountyohio.gov/api/submissions", headers={"User-Agent": UA}, timeout=60).json()
        fc = [s for s in subs if "oreclos" in (s.get("caseCategoryDescription") or "")][:2]
        for s in fc:
            u = "https://clerknewfiling.franklincountyohio.gov" + s["url"]
            r = requests.get(u, headers={"User-Agent": UA}, timeout=60)
            report["pages"][u] = {"status": r.status_code, "type": r.headers.get("content-type"), "len": len(r.content),
                                  "head": r.content[:600].decode("latin-1")}
            PAGES.append(u)
    except Exception as err:
        report["filings_error"] = str(err)[:300]
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        for u in PAGES:
            report["pages"].setdefault(u, {})["browser"] = watch(u, b)
            print(f"Probe: {u[:90]} -> {len(report['pages'][u]['browser']['calls'])} calls")
        b.close()
    OUT.write_text(json.dumps(report, indent=1))
