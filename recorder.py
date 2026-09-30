"""
County Recorder filings that mention Grandview Heights.

The Franklin County Recorder's records are searchable through its Cloud Search (PublicSearch,
franklin.oh.publicsearch.us). Each run searches real property records mentioning "GRANDVIEW
HEIGHTS" recorded over the last week (the first run goes back about four months), reads the
results table, and keeps each document's type, recording date, instrument number, parcel numbers
from the legal description, and a short legal description. Names are kept only for organizations
(companies, banks, governments, trusts); people's names are shown as "Individual", since for a
mortgage or release they're usually the homeowners. Writes docs/recorder.json. Runs from scrape.py.
"""

import json
import re
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote

OUT = Path("docs/recorder.json")
SITE = "https://franklin.oh.publicsearch.us/"
SEARCH = "GRANDVIEW HEIGHTS"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
FIRST_DAYS, OVERLAP_DAYS, PAGE = 120, 7, 100
ORG = re.compile(r"\b(LLC|L L C|INC|CORP\w*|COMPANY|CO|BANK|TRUST\w*|MORTGAGE|FEDERAL|CREDIT UNION|ASSOC\w*|LP|LTD|LLP|PLL?C|CITY|COUNTY|"
                 r"STATE|OHIO|UNITED STATES|FUND|HOLDINGS?|PARTNERS\w*|GROUP|SERVICES?|FINANCIAL|NATIONAL|CHURCH|UNIVERSITY|"
                 r"SCHOOLS?|AUTHORITY|DEPARTMENT|TREASURER|SECRETARY|N ?A|FSB|LENDING|LOANS?|REALTY|PROPERTIES|INVESTMENTS?|"
                 r"DEVELOPMENT|MANAGEMENT|ENTERPRISES?|VENTURES?|CONDOMINIUM|HOMEOWNERS|UTILIT\w+|ELECTRIC|GAS|POWER|ENERGY|"
                 r"MERS|REGISTRATION SYSTEMS|SERVICING|SAVINGS|INSURANCE|TITLE|ESCROW|CAPITAL|EQUITY|ACQUISITION\w*)\b", re.I)
PARCEL = re.compile(r"\b(0?3[05])-?(\d{6})-?(\d{2})\b")
ROWS_JS = """() => {
  const t = document.querySelector('table');
  if (!t) return null;
  const head = [...t.querySelectorAll('thead th')].map(th => th.innerText.trim());
  const rows = [...t.querySelectorAll('tbody tr')].map(tr => [...tr.querySelectorAll('td')].map(td => td.innerText.trim()));
  return {head, rows};
}"""


def names(cell):
    out = []
    for n in re.split(r"\n+", cell or ""):
        n = re.sub(r"\s+", " ", n).strip()
        if n and n not in ("--", "-"):
            out.append(n if ORG.search(n) else "Individual")
    return list(dict.fromkeys(out))


def search_url(start, end, offset):
    return (f"{SITE}results?department=RP&limit={PAGE}&offset={offset}&recordedDateRange={start:%Y%m%d}%2C{end:%Y%m%d}"
            f"&searchOcrText=false&searchType=quickSearch&searchValue={quote(SEARCH)}")


def read_window(page, start, end):
    docs, offset = [], 0
    while offset < 2000:
        page.goto(search_url(start, end, offset), wait_until="domcontentloaded", timeout=60000)
        try:
            page.wait_for_selector("table tbody tr", timeout=20000)
        except Exception:
            break   # no results
        t = page.evaluate(ROWS_JS)
        if not t or not t["rows"]:
            break
        head = [h.upper() for h in t["head"]]
        if offset == 0:
            print(f"  recorder columns: {t['head']}")
        col = lambda name: next((i for i, h in enumerate(head) if name in h), None)
        c = {k: col(k) for k in ("GRANTOR", "GRANTEE", "DOC TYPE", "RECORDED", "INST", "LEGAL")}
        for r in t["rows"]:
            cell = lambda k: r[c[k]] if c[k] is not None and c[k] < len(r) else ""
            if not cell("INST"):
                continue
            legal = re.sub(r"\s+", " ", cell("LEGAL"))
            m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", cell("RECORDED"))
            docs.append({"inst": cell("INST").split()[0], "type": cell("DOC TYPE").title(),
                         "date": f"{m[3]}-{int(m[1]):02d}-{int(m[2]):02d}" if m else None,
                         "from": names(cell("GRANTOR")), "to": names(cell("GRANTEE")),
                         "parcels": list(dict.fromkeys(f"{a.zfill(3)}-{b}-{c_}" for a, b, c_ in PARCEL.findall(legal))),
                         "legal": legal[:180]})
        if len(t["rows"]) < PAGE:
            break
        offset += PAGE
    return docs


def run(state, now):
    data = json.loads(OUT.read_text()) if OUT.exists() else {"documents": []}
    today = date.today()
    through = state.get("recorder_through")
    start = (date.fromisoformat(through) - timedelta(days=OVERLAP_DAYS)) if through else today - timedelta(days=FIRST_DAYS)
    windows, s = [], start
    while s <= today:   # month-sized windows keep each search's results small
        windows.append((s, min(s + timedelta(days=30), today)))
        s += timedelta(days=31)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Recorder: no browser available")
        return
    docs = {d["inst"]: d for d in data["documents"]}
    new = 0
    with sync_playwright() as p:
        b = p.chromium.launch()
        for a, z in windows:
            ctx = b.new_context(user_agent=UA)   # fresh each time: the site carries the last search over otherwise
            page = ctx.new_page()
            try:
                got = read_window(page, a, z)
                ctx.close()
            except Exception as err:
                print(f"Recorder: {a} to {z} failed ({err})")
                b.close()
                return   # try the same window again next run
            new += sum(1 for d in got if d["inst"] not in docs)
            docs.update({d["inst"]: d for d in got})
            print(f"Recorder: {a} to {z}: {len(got)} documents")
        b.close()
    state["recorder_through"] = today.isoformat()
    data["documents"] = sorted(docs.values(), key=lambda d: (d["date"] or "", d["inst"]), reverse=True)
    data["updated"] = now
    OUT.write_text(json.dumps(data, indent=1))
    print(f"Recorder: {new} new, {len(data['documents'])} on file")
