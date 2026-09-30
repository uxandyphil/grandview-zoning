"""
Foreclosure filings and sheriff sales for Grandview Heights properties.

1. Foreclosure filings: the Franklin County Clerk of Courts posts every newly e-filed civil
   complaint (before its quality review) at clerknewfiling.franklincountyohio.gov, with a
   public JSON feed. Each run checks the foreclosure complaints it hasn't seen, reads the
   complaint, and keeps the ones for a Grandview property (the complaint names Grandview
   Heights or a Grandview parcel, tax districts 030 and 035). Saved: filing date, the
   plaintiff (usually the lender), the property address and parcel. Defendants' names aren't
   saved; they're usually the homeowners.
2. Sheriff sales: the Sheriff's online auction site (RealForeclose) shows a public preview of
   each Friday's sale: case, parcel, address, appraisal, opening bid, and status. Each run reads
   the next several Fridays and the last few (for results), plus older weeks a few at a time.

Writes docs/court-filings.json. Runs from scrape.py.
"""

import io
import json
import re
from datetime import date, timedelta
from pathlib import Path

import requests
from pypdf import PdfReader

OUT = Path("docs/court-filings.json")
FILINGS_API = "https://clerknewfiling.franklincountyohio.gov/api/submissions"
AUCTION = "https://franklin.sheriffsaleauction.ohio.gov/index.cfm?zaction=AUCTION&zmethod=PREVIEW&AuctionDate="
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
HEADERS = {"User-Agent": UA}
GRANDVIEW_PARCEL = re.compile(r"\b0?3[05][-\s]?\d{6}[-\s]?\d{2}\b")
FUTURE_WEEKS, RECENT_WEEKS, BACKFILL_PER_RUN, BACKFILL_WEEKS = 6, 3, 8, 104
MAX_COMPLAINTS_PER_RUN = 40


def parcel_fmt(v):
    d = re.sub(r"\D", "", str(v or ""))
    if len(d) == 9:          # auction site drops the last two digits
        d += "00"
    return f"{d[:3]}-{d[3:9]}-{d[9:11]}" if len(d) >= 11 else None


# ---------- 1. foreclosure filings ----------

def document_urls(listing):
    """PDF links from a submission's documents response, whatever shape it has."""
    urls = []
    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if (isinstance(v, str) and v.startswith(("/", "http")) and re.search(r"\.pdf|/documents?/|download|/file", v, re.I)
                        and not v.endswith("/documents")):
                    urls.append(v)
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(listing)
    return [requests.compat.urljoin(FILINGS_API, u) for u in dict.fromkeys(urls)]


def complaint_text(sub):
    """Text of the complaint (first PDF) for a submission."""
    r = requests.get(requests.compat.urljoin(FILINGS_API, sub["url"]), headers=HEADERS, timeout=60)
    r.raise_for_status()
    if r.content.startswith(b"%PDF"):
        pdf = r.content
    else:
        urls = document_urls(r.json())
        if not urls:
            raise ValueError(f"no document links in {str(r.text)[:200]}")
        d = requests.get(urls[0], headers=HEADERS, timeout=90)
        d.raise_for_status()
        pdf = d.content
    if not pdf.startswith(b"%PDF"):
        raise ValueError("document isn't a PDF")
    return "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(pdf)).pages[:8])


def grandview_property(text, address_re):
    """(address, parcel) if the complaint is about a Grandview Heights property, else None."""
    flat = re.sub(r"\s+", " ", text)
    parcel = next((parcel_fmt(m[0]) for m in GRANDVIEW_PARCEL.finditer(flat)), None)
    gv = [m for m in re.finditer(r"Grandview\s+(Heights|Hts)", flat, re.I)]
    if not parcel and not gv:
        return None
    address = ""
    for m in gv:   # the street address just before "Grandview Heights"
        hits = list(address_re.finditer(flat[max(0, m.start() - 120):m.start()]))
        if hits:
            address = f"{hits[-1][1]} {hits[-1][2]}".strip()
            break
    return address, parcel


def run_filings(data, state, address_re):
    seen = state.setdefault("court_seen", [])
    try:
        subs = requests.get(FILINGS_API, headers=HEADERS, timeout=60).json()
    except (requests.RequestException, ValueError) as err:
        print(f"Foreclosure filings: feed unavailable ({err})")
        return
    fc = [s for s in subs if re.search(r"foreclos", s.get("caseCategoryDescription") or "", re.I)]
    todo = [s for s in fc if s["submissionId"] not in seen][:MAX_COMPLAINTS_PER_RUN]
    print(f"Foreclosure filings: {len(subs)} new civil filings in the feed, {len(fc)} foreclosures, {len(todo)} to read")
    have = {f["id"] for f in data["filings"]}
    found = 0
    for s in todo:
        try:
            hit = grandview_property(complaint_text(s), address_re)
        except Exception as err:
            print(f"  {s['submissionId']}: {err}")
            continue          # try again next run
        seen.append(s["submissionId"])
        if hit and s["submissionId"] not in have:
            data["filings"].append({"id": s["submissionId"], "date": (s.get("createDate") or "")[:10],
                                    "plaintiff": re.split(r"\s+-?VS\.?-?\s+", s.get("caseStyle") or "", flags=re.I)[0].title(),
                                    "category": s.get("caseCategoryDescription"), "address": hit[0], "parcel": hit[1]})
            found += 1
    del seen[:-3000]
    print(f"  {found} new Grandview foreclosure filings")


# ---------- 2. sheriff sales ----------

def fridays(center, back, ahead):
    f = center + timedelta(days=(4 - center.weekday()) % 7)
    return [f + timedelta(weeks=i) for i in range(-back, ahead + 1)]


def parse_sale_text(body):
    """Sales from the preview page's text. Each sale starts with a status header ("Auction Starts",
    "Auction Status", "Auction Sold") followed by tab-separated "Label:\tvalue" lines."""
    items = []
    for chunk in re.split(r"(?=\bAuction (?:Starts|Status|Sold)\b)", body)[1:]:
        head, _, rest = chunk.partition("Case Status:")
        if not rest:
            continue
        fields, last = {}, None
        for line in ("Case Status:" + rest).split("\n"):
            if ":" in line and not line.startswith(("\t", " ")) and line.split(":", 1)[0].strip():
                last, v = line.split(":", 1)
                last = last.strip().lower()
                fields[last] = v.strip()
            elif last and line.strip():   # a continuation line (the city and zip under the address)
                fields[last] += "\n" + line.strip()
        head = re.sub(r"\s+", " ", head).strip()
        num = lambda k: int(float(re.sub(r"[^\d.]", "", fields.get(k, "")) or 0)) or None
        addr = fields.get("property address", "").split("\n")
        status = ("Sold" if re.search(r"\bsold\b", head, re.I) else "Scheduled" if head.startswith("Auction Starts")
                  else re.sub(r"^Auction Status\s*", "", head).split(" ")[0] or "Unknown")
        amount = re.search(r"Amount\s*\$?([\d,]+(?:\.\d\d)?)", head)
        sold_to = re.search(r"Sold To\s*(3rd Party Bidder|Plaintiff)", head, re.I)
        items.append({"case": fields.get("case #", "").split(" (")[0].strip(), "parcel": parcel_fmt(fields.get("parcel id")),
                      "address": addr[0].strip().title(), "city": addr[1].split(",")[0].strip().title() if len(addr) > 1 else "",
                      "appraised": num("appraised value"), "opening_bid": num("opening bid"), "status": status.title(),
                      "sold_for": int(float(amount[1].replace(",", ""))) if amount else None,
                      "sold_to": ("Plaintiff" if sold_to and sold_to[1].lower() == "plaintiff" else "3rd party bidder" if sold_to else None)})
    return items


def run_sales(data, state):
    done = set(state.setdefault("sheriff_weeks_done", []))
    today = date.today()
    weeks = fridays(today, RECENT_WEEKS, FUTURE_WEEKS)
    older = [d for d in fridays(today, BACKFILL_WEEKS, -RECENT_WEEKS - 1) if d.isoformat() not in done][-BACKFILL_PER_RUN:]
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Sheriff sales: no browser available")
        return
    sales = {(s["case"], s["date"]): s for s in data["sales"]}
    counts = {}
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(user_agent=UA)
        for d in weeks + older:
            try:
                page.goto(AUCTION + d.strftime("%m/%d/%Y"), wait_until="domcontentloaded", timeout=60000)
                try:   # the sale list loads after the page; a week with no sale never shows one
                    page.wait_for_function("document.body.innerText.includes('Case Status') || document.body.innerText.includes('no cases')", timeout=20000)
                except Exception:
                    page.wait_for_timeout(3000)
                items = parse_sale_text(page.evaluate("document.body.innerText"))
            except Exception as err:
                print(f"  sheriff sale {d}: {err}")
                continue
            counts[d.isoformat()] = len(items)
            for it in items:
                if (it["parcel"] or "")[:3] in ("030", "035") or re.search(r"grandview", it["city"], re.I):
                    sales[(it["case"], d.isoformat())] = {**it, "date": d.isoformat()}
            if d < today - timedelta(weeks=RECENT_WEEKS):
                done.add(d.isoformat())
        b.close()
    data["sales"] = sorted(sales.values(), key=lambda s: s["date"], reverse=True)
    state["sheriff_weeks_done"] = sorted(done)
    print(f"Sheriff sales: {sum(counts.values())} items in {len(counts)} weeks checked, {len(data['sales'])} Grandview sales on file")


def run(state, geocode, now, address_re):
    data = json.loads(OUT.read_text()) if OUT.exists() else {"filings": [], "sales": []}
    for step in (lambda: run_filings(data, state, address_re), lambda: run_sales(data, state)):
        try:
            step()
        except Exception as err:
            print(f"Court filings step failed: {err}")
    for rec in data["filings"] + data["sales"]:
        if rec.get("address") and rec.get("lat") is None and not rec.get("nomap"):
            c = geocode(rec["address"] + ", Grandview Heights, OH")
            if c:
                rec["lat"], rec["lon"] = c
            else:
                rec["nomap"] = True
    data["updated"] = now
    OUT.write_text(json.dumps(data, indent=1))
