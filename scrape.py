"""
Grandview Heights zoning watch

Collects Board of Zoning Appeals and Planning Commission cases from two
places on the city's website:

  1. Archive Center: older agendas (plain HTML list of PDFs)
  2. Document Center: current-year case documents (the folder list loads
     with JavaScript, so we open it in a headless browser)

Each case address is geocoded and written to docs/cases.json for the site.
Building permits from the OpenGov portal go to docs/permits.json (permits.py).
Run daily by .github/workflows/update.yml. See README.md.
"""

import io
import json
import os
import re
import smtplib
import sys
import time
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import urljoin

import requests
from pypdf import PdfReader

BASE = "https://www.grandviewheights.gov/"
ARCHIVE_INDEX = BASE + "Archive.aspx"
FALLBACK_ARCHIVES = {"Board of Zoning Appeals": BASE + "Archive.aspx?AMID=40"}
BOARD_PATTERNS = {
    "Board of Zoning Appeals": re.compile(r"zoning appeals|\bBZA\b", re.I),
    "Planning Commission": re.compile(r"planning", re.I),
}
# Document Center folders linked from the city's Public Documents page
DOC_FOLDERS = {"Board of Zoning Appeals": 173, "Planning Commission": 41}
MAX_SUBFOLDER_DEPTH = 3
MAX_FOLDERS = 80

CITY_SUFFIX = ", Grandview Heights, OH 43212"
STATE_FILE = Path("state.json")
CASES_FILE = Path("docs/cases.json")
MAX_BACKFILL = int(os.environ.get("MAX_BACKFILL", "40"))
MAX_PDF_BYTES = 12_000_000
DRY_RUN = "--dry-run" in sys.argv

UA = "grandview-zoning-watch/1.1 (community site, runs once daily)"
HEADERS = {"User-Agent": UA}
PAUSE_SECONDS = 2

STREET_SUFFIX = (
    r"(?:Ave(?:nue)?|St(?:reet)?|Rd|Road|Blvd|Boulevard|Dr(?:ive)?|Ct|Court|"
    r"Pl(?:ace)?|Way|Ln|Lane|Pkwy|Parkway|Cir(?:cle)?|Ter(?:race)?)"
)
ADDRESS_RE = re.compile(
    r"\b(\d{2,5})\s+((?:[NSEW]\.?\s+)?(?:(?:\d+(?:st|nd|rd|th)|[A-Za-z][A-Za-z']*)\.?\s+){1,3}?"
    + STREET_SUFFIX + r")\.?\b",
    re.I,
)
NOT_ADDRESS_WORDS = {"feet", "foot", "ft", "percent", "section", "sq", "square"}
CITY_HALL = {"1525 goodale", "1260 mckinley", "1515 goodale", "1515 w goodale", "1525 w goodale"}

MONTHS = ("January|February|March|April|May|June|July|August|September|October|"
          "November|December")
DATE_PATTERNS = [
    (re.compile(rf"({MONTHS})\s+(\d{{1,2}}),?\s+(\d{{4}})"), lambda m: f"{m[1]} {m[2]} {m[3]}", "%B %d %Y"),
    (re.compile(r"\b(20\d\d)[-_.](\d{1,2})[-_.](\d{1,2})\b"), lambda m: f"{m[1]}-{m[2]}-{m[3]}", "%Y-%m-%d"),
    (re.compile(r"\b(\d{1,2})[-_./](\d{1,2})[-_./](20\d\d)\b"), lambda m: f"{m[1]}-{m[2]}-{m[3]}", "%m-%d-%Y"),
    (re.compile(r"\b(\d{2})(\d{2})(20\d\d)\b"), lambda m: f"{m[1]}-{m[2]}-{m[3]}", "%m-%d-%Y"),
]
# Document Center subfolders worth opening (meeting dates, years, cases)
SUBFOLDER_HINT = re.compile(rf"20\d\d|{MONTHS}|\bcase|\bBZA\b|planning|agenda|\d{{2,5}}\s+\w", re.I)
# Non-agenda documents worth opening to find the case address
CASE_DOC_HINT = re.compile(r"staff|report|application|notice|memo", re.I)
SKIP_DOC_HINT = re.compile(r"plan|drawing|elevation|survey|photo|render|site|minutes|fillable|form\b", re.I)
ADDRESS_LABEL_RE = re.compile(r"(address of request|property address|subject property|location)\s*:?", re.I)


# ---------- helpers ----------

def get(url):
    time.sleep(PAUSE_SECONDS)
    resp = requests.get(url, headers=HEADERS, timeout=45)
    resp.raise_for_status()
    return resp


def load_json(path, default):
    return json.loads(path.read_text()) if path.exists() else default


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True))


def parse_date(text):
    """First recognizable date in text, as YYYY-MM-DD."""
    for pattern, joiner, fmt in DATE_PATTERNS:
        for m in pattern.finditer(text or ""):
            try:
                return datetime.strptime(joiner(m), fmt).date().isoformat()
            except ValueError:
                continue
    return None


ORDINALS = {"first": "1st", "second": "2nd", "third": "3rd", "fourth": "4th", "fifth": "5th",
            "sixth": "6th", "seventh": "7th", "eighth": "8th", "ninth": "9th", "tenth": "10th"}
SUFFIXES = {"avenue": "ave", "street": "st", "road": "rd", "boulevard": "blvd", "drive": "dr",
            "court": "ct", "place": "pl", "lane": "ln", "parkway": "pkwy", "circle": "cir",
            "terrace": "ter", "west": "w", "east": "e", "north": "n", "south": "s"}


def norm_address(address):
    words = re.sub(r"[^a-z0-9 ]", " ", address.lower()).split()
    return " ".join(SUFFIXES.get(w, ORDINALS.get(w, w)) for w in words)


def title_case(address):
    return address if not address.isupper() else " ".join(
        w if re.match(r"^\d", w) else w.capitalize() for w in address.split())


# ---------- parsing ----------

def pdf_pages(url, max_bytes=MAX_PDF_BYTES):
    resp = get(url)
    if len(resp.content) > max_bytes:
        raise ValueError("file too large")
    if not resp.content.startswith(b"%PDF"):
        raise ValueError("not a PDF")
    return [page.extract_text() or "" for page in PdfReader(io.BytesIO(resp.content)).pages]


def pdf_text(url):
    return "\n".join(pdf_pages(url))


def packet_agenda_text(url):
    """A meeting packet starts with the agenda, then applications (which list
    neighbors' addresses). Keep only the agenda pages: up to ADJOURNMENT."""
    pages = pdf_pages(url, max_bytes=60_000_000)
    kept = []
    for page in pages[:6]:
        kept.append(page)
        if re.search(r"ADJOURN", page, re.I):
            break
    return "\n".join(kept)


def find_addresses(text):
    """[(match, address)] for plausible street addresses, skipping city hall."""
    flat = re.sub(r"\s+", " ", text or "")
    hits = []
    for m in ADDRESS_RE.finditer(flat):
        words = m.group(2).lower().split()
        if any(w.strip(".") in NOT_ADDRESS_WORDS for w in words):
            continue
        address = f"{m.group(1)} {m.group(2)}".strip().rstrip(".")
        if any(norm_address(address).startswith(h) for h in CITY_HALL):
            continue
        hits.append((m, address))
    return flat, hits


def extract_cases(text):
    """From an agenda: [(address, description)], description = text up to the next address."""
    flat, hits = find_addresses(text)
    cases, seen = [], set()
    for i, (m, address) in enumerate(hits):
        key = norm_address(address)
        if key in seen:
            continue
        seen.add(key)
        end = hits[i + 1][0].start() if i + 1 < len(hits) else len(flat)
        desc = flat[m.end():min(end, m.end() + 700)]
        desc = re.sub(r"(B\.?Z\.?A\.?|P\.?C\.?)?\s*Case[\s#:\w.-]*$", "", desc, flags=re.I)
        desc = re.split(r"\b(ADJOURN|OTHER BUSINESS|STAFF COMMUNICATIONS)", desc, flags=re.I)[0]
        cases.append((address, desc.strip(" .,:;-")))
    return cases


def case_address_from_document(text):
    """From an application or staff report: the subject property, not neighbors' addresses."""
    flat, hits = find_addresses(text)
    label = ADDRESS_LABEL_RE.search(flat)
    if label:
        for m, address in hits:
            if 0 <= m.start() - label.end() < 120:
                return address
    head = [a for m, a in hits if m.start() < 1500]
    return head[0] if head else None


# ---------- source 1: Archive Center ----------

def discover_archives():
    try:
        html = get(ARCHIVE_INDEX).text
    except requests.RequestException as err:
        print(f"Archive index unavailable ({err}); using fallback")
        return dict(FALLBACK_ARCHIVES)
    found = {}
    for m in re.finditer(r'href="([^"]*AMID=(\d+)[^"]*)"[^>]*>(.*?)</a>', html, re.S):
        name = re.sub(r"<[^>]+>|\s+", " ", m.group(3)).strip()
        if "agenda" not in name.lower():
            continue
        for board, pattern in BOARD_PATTERNS.items():
            if pattern.search(name) and board not in found:
                found[board] = urljoin(BASE, m.group(1).replace("&amp;", "&"))
    for board, url in FALLBACK_ARCHIVES.items():
        found.setdefault(board, url)
    print("Archives:", found)
    return found


def list_archive_items(archive_url):
    html = get(archive_url).text
    items = {}
    for m in re.finditer(r'href="([^"]*(?:ADID=|ViewFile/Item/)(\d+)[^"]*)"', html):
        items[m.group(2)] = urljoin(BASE, m.group(1).replace("&amp;", "&"))
    return items


# ---------- source 2: Document Center (needs a browser) ----------

def crawl_document_center():
    """Return [{board, doc_id, name, url, folder}] for every document in the case folders."""
    from playwright.sync_api import sync_playwright

    docs, visited = {}, set()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(user_agent=UA)

        def links_on(url):
            time.sleep(PAUSE_SECONDS)
            page.goto(url, wait_until="networkidle", timeout=60_000)
            page.wait_for_timeout(1500)
            return page.eval_on_selector_all(
                "a[href*='DocumentCenter/']",
                "els => els.map(e => ({href: e.href, text: (e.textContent || '').trim()}))")

        # Folders visible from the Document Center home are top-level; never crawl those
        baseline = {m.group(1) for l in links_on(BASE + "DocumentCenter")
                    if (m := re.search(r"DocumentCenter/Index/(\d+)", l["href"]))}

        for board, root in DOC_FOLDERS.items():
            queue = [(str(root), board, 0)]
            while queue and len(visited) < MAX_FOLDERS:
                folder_id, folder_name, depth = queue.pop(0)
                if folder_id in visited:
                    continue
                visited.add(folder_id)
                try:
                    links = links_on(f"{BASE}DocumentCenter/Index/{folder_id}")
                except Exception as err:
                    print(f"  folder {folder_id} failed: {err}")
                    continue
                n_docs = 0
                for link in links:
                    href, text = link["href"], link["text"]
                    if m := re.search(r"DocumentCenter/View/(\d+)", href):
                        if m.group(1) not in docs:
                            docs[m.group(1)] = {"board": board, "doc_id": m.group(1),
                                                "name": text or href.rsplit("/", 1)[-1],
                                                "url": href.split("?")[0], "folder": folder_name}
                            n_docs += 1
                    elif m := re.search(r"DocumentCenter/Index/(\d+)", href):
                        sub = m.group(1)
                        if (sub not in visited and sub not in baseline and depth < MAX_SUBFOLDER_DEPTH
                                and SUBFOLDER_HINT.search(text)):
                            queue.append((sub, text, depth + 1))
                print(f"  {board}: folder {folder_id} '{folder_name}' -> {n_docs} new docs")
        browser.close()
    print(f"Document Center: {len(docs)} documents in {len(visited)} folders")
    return list(docs.values())


# ---------- geocoding ----------

def geocode(address, cache):
    if address in cache:
        return cache[address]
    url = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
    params = {"address": address, "benchmark": "Public_AR_Current", "format": "json"}
    try:
        matches = requests.get(url, params=params, headers=HEADERS, timeout=30).json()[
            "result"]["addressMatches"]
    except (requests.RequestException, KeyError, ValueError):
        return None
    coords = [matches[0]["coordinates"]["y"], matches[0]["coordinates"]["x"]] if matches else None
    cache[address] = coords
    return coords


# ---------- case store ----------

class CaseStore:
    """Cases keyed by meeting date + normalized address, so both sources merge."""

    def __init__(self, existing, geocache, now):
        self.cases, self.geocache, self.now, self.new = {}, geocache, now, []
        for c in existing:
            c.setdefault("documents", [])
            self.cases[self.key(c["meeting_date"], c["address"])] = c

    @staticmethod
    def key(date, address):
        return f"{date or 'nodate'}|{norm_address(address)}"

    def add(self, board, address, date, description="", agenda_url=None, document=None, notify=True):
        k = self.key(date, address)
        case = self.cases.get(k)
        if case is None:
            coords = geocode(address + CITY_SUFFIX, self.geocache)
            case = {"id": k, "board": board, "address": title_case(address),
                    "description": description, "meeting_date": date, "agenda_url": agenda_url,
                    "documents": [], "lat": coords[0] if coords else None,
                    "lon": coords[1] if coords else None, "first_seen": self.now}
            self.cases[k] = case
            if notify:
                self.new.append(case)
        if description and len(description) > len(case.get("description") or ""):
            case["description"] = description
        if agenda_url and not case.get("agenda_url"):
            case["agenda_url"] = agenda_url
        if document and document["url"] not in {d["url"] for d in case["documents"]}:
            case["documents"].append(document)

    def ordered(self):
        return sorted(self.cases.values(),
                      key=lambda c: (c["meeting_date"] or "", c["address"]), reverse=True)


# ---------- email (optional) ----------

def email_digest(new_cases):
    if DRY_RUN or not os.environ.get("SMTP_USER") or not new_cases:
        return
    site = os.environ.get("SITE_URL", "")
    lines = [f"{len(new_cases)} new zoning case(s) in Grandview Heights.", site, ""]
    for c in new_cases:
        link = c.get("agenda_url") or (c["documents"][0]["url"] if c["documents"] else "")
        lines += [f"{c['address']} ({c['board']}, {c['meeting_date'] or 'date TBD'})",
                  (c["description"] or "")[:300], link, ""]
    msg = EmailMessage()
    msg["Subject"] = f"Grandview zoning: {len(new_cases)} new case(s)"
    msg["From"] = os.environ["SMTP_USER"]
    msg["To"] = os.environ.get("EMAIL_TO", os.environ["SMTP_USER"])
    msg.set_content("\n".join(lines))
    with smtplib.SMTP(os.environ.get("SMTP_HOST", "smtp.gmail.com"),
                      int(os.environ.get("SMTP_PORT", "587"))) as smtp:
        smtp.starttls()
        smtp.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
        smtp.send_message(msg)


# ---------- main ----------

def run_archives(store, state, backfill):
    for board, archive_url in discover_archives().items():
        seen = set(state["seen"].get(board, []))
        items = list_archive_items(archive_url)
        todo = sorted((i for i in items if i not in seen), key=int)
        if backfill and len(todo) > MAX_BACKFILL:
            seen.update(todo[:-MAX_BACKFILL])
            todo = todo[-MAX_BACKFILL:]
        print(f"Archive {board}: {len(items)} agendas, processing {len(todo)}")
        for item_id in todo:
            url = items[item_id]
            try:
                text = pdf_text(url)
            except Exception as err:
                print(f"  skip {url}: {err}")
                continue
            date = parse_date(text)
            for address, desc in extract_cases(text):
                store.add(board, address, date, desc, agenda_url=url, notify=not backfill)
            seen.add(item_id)
        state["seen"][board] = sorted(seen, key=int)


def run_document_center(store, state):
    state["seen"].pop("doccenter", None)  # v1 key; v2 re-reads packets
    backfill = "doccenter_v2" not in state["seen"]
    seen = set(state["seen"].get("doccenter_v2", []))
    try:
        docs = crawl_document_center()
    except Exception as err:
        print(f"Document Center crawl failed: {err}")
        return
    for doc in docs:
        if doc["doc_id"] in seen:
            continue
        name, folder, url, board = doc["name"], doc["folder"], doc["url"], doc["board"]
        date = parse_date(name) or parse_date(folder)
        document = {"name": name, "url": url}
        try:
            if re.search(r"agenda|packet", name, re.I):
                is_packet = re.search(r"packet", name, re.I)
                text = packet_agenda_text(url) if is_packet else pdf_text(url)
                date = date or parse_date(text)
                for address, desc in extract_cases(text):
                    store.add(board, address, date, desc, agenda_url=url, notify=not backfill)
            else:
                address = next((a for _, a in find_addresses(name)[1]), None) \
                    or next((a for _, a in find_addresses(folder)[1]), None)
                if SKIP_DOC_HINT.search(name):
                    seen.add(doc["doc_id"])
                    continue  # blank forms, plans, photos
                if not address and CASE_DOC_HINT.search(name):
                    text = pdf_text(url)
                    date = date or parse_date(text)
                    address = case_address_from_document(text)
                if address:
                    store.add(board, address, date, document=document, notify=not backfill)
                else:
                    print(f"  no address for '{name}' in '{folder}'")
        except Exception as err:
            print(f"  skip '{name}': {err}")
            continue
        seen.add(doc["doc_id"])
    state["seen"]["doccenter_v2"] = sorted(seen, key=int)


def main():
    state = load_json(STATE_FILE, None)
    first_run = state is None
    state = state or {"seen": {}, "geocache": {}}
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    store = CaseStore(load_json(CASES_FILE, {"cases": []})["cases"], state["geocache"], now)

    run_archives(store, state, backfill=first_run)
    run_document_center(store, state)

    ordered = store.ordered()
    save_json(CASES_FILE, {"updated": now, "cases": ordered})

    # Building permits live in their own file; a failure here never blocks the cases
    try:
        import permits
        permits.run(state, lambda address: geocode(
            address if re.search(r"\bOH\b", address) else address + CITY_SUFFIX, state["geocache"]), now)
    except Exception as err:
        print(f"Permits step failed: {err}")

    # County auditor permits: monthly public file, long history and estimated costs
    try:
        import county_permits
        county_permits.run(state, lambda address: geocode(
            address if re.search(r"\bOH\b", address) else address + CITY_SUFFIX, state["geocache"]), now)
    except Exception as err:
        print(f"County permits step failed: {err}")

    # County Recorder documents mentioning Grandview Heights (PublicSearch)
    try:
        import recorder
        recorder.run(state, now)
    except Exception as err:
        print(f"Recorder step failed: {err}")

    # City Council agendas from the meeting packets in the Document Center, summarized by item
    try:
        import council
        council.run(state, now, parse_date, packet_agenda_text)
    except Exception as err:
        print(f"Council step failed: {err}")

    # Foreclosure filings (Clerk of Courts) and sheriff sales for Grandview properties
    try:
        import court_filings
        court_filings.run(state, lambda address: geocode(address, state["geocache"]), now, ADDRESS_RE)
    except Exception as err:
        print(f"Court filings step failed: {err}")

    # New business filings from Ohio Secretary of State reports uploaded to sos-uploads/
    try:
        import sos_filings
        sos_filings.run(state, lambda address: geocode(address, state["geocache"]), now)
    except Exception as err:
        print(f"Business filings step failed: {err}")
    save_json(STATE_FILE, state)
    print(f"{len(store.new)} new case(s), {len(ordered)} total")
    email_digest(store.new)


if __name__ == "__main__":
    main()
