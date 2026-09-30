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
import zipfile
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
VERSION = 2   # bump to read the feed's foreclosure complaints again


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
    elif r.content.startswith(b"PK"):   # a zip of the filing's PDFs; the complaint comes first
        z = zipfile.ZipFile(io.BytesIO(r.content))
        names = sorted((n for n in z.namelist() if n.lower().endswith(".pdf")), key=lambda n: z.getinfo(n).filename)
        if not names:
            raise ValueError("no PDF in the zip")
        text = ""
        for n in names[:3]:   # the complaint, and sometimes a separate exhibit with the legal description
            text += "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(z.read(n))).pages[:8]) + "\n"
            if re.search(r"Grandview|0?3[05]-\d{6}", text, re.I):
                break
        return text
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
    if state.get("court_version") != VERSION:
        state["court_seen"], state["court_version"] = [], VERSION
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
    found = read = 0
    for s in todo:
        try:
            text = complaint_text(s)
            read += len(text) > 500
            hit = grandview_property(text, address_re)
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
    print(f"  {read} complaints read, {found} new Grandview foreclosure filings")


# ---------- 2. sheriff sales ----------

def fridays(center, back, ahead):
    f = center + timedelta(days=(4 - center.weekday()) % 7)
    return [f + timedelta(weeks=i) for i in range(-back, ahead + 1)]


SALE_LABELS = ["Case Status", "Case #", "Parcel ID", "Property Address", "Appraised Value", "Opening Bid", "Deposit Requirement",
               "Assessed Value", "Plaintiff Max Bid"]


def parse_sale_text(body):
    """Sales from the preview page's text. Each sale is a run of "Label: value" pairs starting with
    "Case Status:". What comes just before it says how it went: "Auction Starts" (waiting),
    "Auction Status Withdrawn", "Auction Sold ... Amount ... Sold To ...", or nothing in the closed list."""
    body = body.replace("\u00a0", " ")
    alt = "|".join(re.escape(l) for l in SALE_LABELS)
    parts = re.split(r"(?=Case\s+Status\s*:)", body)
    items = []
    for i, chunk in enumerate(parts[1:], 1):
        # the header for this sale is the tail of the previous part, after its last "Label: value" line
        prev = parts[i - 1]
        tail = prev[max(prev.rfind("Deposit Requirement"), prev.rfind("Opening Bid"), prev.rfind("Preview Items"), 0):]
        tail = re.sub(r"^(Deposit Requirement|Opening Bid)\s*:\s*\S+", "", tail)
        head = re.sub(r"\s+", " ", tail).strip()
        field = lambda lab: (re.search(rf"{re.escape(lab)}\s*:\s*(.*?)(?=(?:{alt})\s*:|Auction\s+(?:Starts|Status|Sold)|\Z)", chunk, re.S)
                             or [None, ""])[1].strip()
        num = lambda lab: int(float(re.sub(r"[^\d.]", "", field(lab)) or 0)) or None
        addr = [l.strip() for l in re.split(r"\n|\t", field("Property Address")) if l.strip()]
        st = re.search(r"Auction\s+Status\s+(\w+)", head)
        status = ("Sold" if re.search(r"Auction\s+Sold|\bSold\s+To\b", head, re.I) else "Scheduled" if re.search(r"Auction\s+Starts", head)
                  else st[1] if st else "Closed" if "Closed or Canceled" in body[:body.find(chunk)] else "Unknown")
        amount = re.search(r"Amount\s*:?\s*\$?([\d,]+(?:\.\d\d)?)", head)
        sold_to = re.search(r"Sold\s+To\s*:?\s*(3rd Party Bidder|Plaintiff)", head, re.I)
        items.append({"case": field("Case #").split("(")[0].strip(), "parcel": parcel_fmt(field("Parcel ID")),
                      "address": addr[0].title() if addr else "", "city": addr[1].split(",")[0].strip().title() if len(addr) > 1 else "",
                      "appraised": num("Appraised Value"), "opening_bid": num("Opening Bid"), "status": status.title(),
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
                    # (the "running now" box always says "no cases", so wait for an actual sale)
                    page.wait_for_function("document.body.innerText.includes('Case Status')", timeout=20000)
                except Exception:
                    page.wait_for_timeout(3000)
                body = page.evaluate("document.body.innerText")
                items = parse_sale_text(body)
                if not items and sum(1 for v in counts.values() if v == 0) < 2:
                    at = body.find("Preview Items")
                    print(f"  sheriff sale {d}: no sales read; page text: {re.sub(chr(10) + '+', ' / ', body[at:at + 1500])!r}")
            except Exception as err:
                print(f"  sheriff sale {d}: {err}")
                continue
            counts[d.isoformat()] = len(items)
            print(f"  sheriff sale {d}: {len(items)} sales")
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
