"""
Grandview Heights zoning watch

Finds every Board of Zoning Appeals and Planning Commission agenda the
city posts, pulls each case address out of the PDF, geocodes it, and
writes docs/cases.json for the website. Optionally emails a digest of
new cases.

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

CITY_SUFFIX = ", Grandview Heights, OH 43212"
STATE_FILE = Path("state.json")
CASES_FILE = Path("docs/cases.json")
MAX_BACKFILL = int(os.environ.get("MAX_BACKFILL", "40"))  # agendas per board on first run
DRY_RUN = "--dry-run" in sys.argv

HEADERS = {"User-Agent": "grandview-zoning-watch/1.0 (community site, runs once daily)"}
PAUSE_SECONDS = 2

STREET_SUFFIX = (
    r"(?:Ave(?:nue)?|St(?:reet)?|Rd|Road|Blvd|Boulevard|Dr(?:ive)?|Ct|Court|"
    r"Pl(?:ace)?|Way|Ln|Lane|Pkwy|Parkway|Cir(?:cle)?|Ter(?:race)?)"
)
ADDRESS_RE = re.compile(
    r"\b(\d{2,5})\s+((?:[NSEW]\.?\s+)?(?:(?:\d+(?:st|nd|rd|th)|[A-Za-z][A-Za-z']*)\.?\s+){1,3}?" + STREET_SUFFIX + r")\.?\b",
    re.I,
)
NOT_ADDRESS_WORDS = {"feet", "foot", "ft", "percent", "section", "sq", "square"}
CITY_HALL = {"1525 goodale", "1260 mckinley", "1515 goodale", "1515 w goodale", "1525 w goodale"}
DATE_RE = re.compile(
    r"(January|February|March|April|May|June|July|August|September|October|"
    r"November|December)\s+(\d{1,2}),?\s+(\d{4})"
)


# ---------- helpers ----------

def get(url):
    time.sleep(PAUSE_SECONDS)
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp


def load_json(path, default):
    return json.loads(path.read_text()) if path.exists() else default


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True))


# ---------- discovery ----------

def discover_archives():
    """Find the city's BZA and Planning Commission agenda archives by name."""
    try:
        html = get(ARCHIVE_INDEX).text
    except requests.RequestException as err:
        print(f"Archive index unavailable ({err}); using fallback")
        return dict(FALLBACK_ARCHIVES)

    found = {}
    for m in re.finditer(r'href="([^"]*AMID=(\d+)[^"]*)"[^>]*>(.*?)</a>', html, re.S):
        name = re.sub(r"<[^>]+>|\s+", " ", m.group(3)).strip()
        if "agenda" not in name.lower():
            continue  # skip minutes archives
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


# ---------- parsing ----------

def pdf_text(url):
    reader = PdfReader(io.BytesIO(get(url).content))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def meeting_date(text):
    m = DATE_RE.search(text)
    if not m:
        return None
    try:
        return datetime.strptime(" ".join(m.groups()), "%B %d %Y").date().isoformat()
    except ValueError:
        return None


def extract_cases(text):
    """Return [(address, description)], description = text up to the next address."""
    flat = re.sub(r"\s+", " ", text)
    hits = []
    for m in ADDRESS_RE.finditer(flat):
        words = m.group(2).lower().split()
        if any(w.strip(".") in NOT_ADDRESS_WORDS for w in words):
            continue
        address = f"{m.group(1)} {m.group(2)}".strip().rstrip(".")
        norm = re.sub(r"[^a-z0-9 ]", "", address.lower())
        if any(norm.startswith(h) for h in CITY_HALL):
            continue  # meeting location, not a case
        hits.append((m, address))

    cases, seen = [], set()
    for i, (m, address) in enumerate(hits):
        key = address.lower()
        if key in seen:
            continue
        seen.add(key)
        end = hits[i + 1][0].start() if i + 1 < len(hits) else len(flat)
        desc = flat[m.end():min(end, m.end() + 700)]
        desc = re.sub(r"(B\.?Z\.?A\.?|P\.?C\.?)?\s*Case[\s#:\w.-]*$", "", desc, flags=re.I)
        desc = desc.strip(" .,:;-")
        cases.append((address, desc))
    return cases


def title_case(address):
    return address if not address.isupper() else " ".join(
        w if re.match(r"^\d", w) else w.capitalize() for w in address.split())


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


# ---------- email (optional) ----------

def email_digest(new_cases):
    if DRY_RUN or not os.environ.get("SMTP_USER"):
        return
    site = os.environ.get("SITE_URL", "")
    lines = [f"{len(new_cases)} new zoning case(s) in Grandview Heights.", site, ""]
    for c in new_cases:
        lines += [f"{c['address']} ({c['board']}, {c['meeting_date'] or 'date TBD'})",
                  c["description"][:300], c["agenda_url"], ""]
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

def main():
    state = load_json(STATE_FILE, None)
    first_run = state is None
    state = state or {"seen": {}, "geocache": {}}
    data = load_json(CASES_FILE, {"cases": []})
    cases = {c["id"]: c for c in data["cases"]}
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    new_cases = []

    for board, archive_url in discover_archives().items():
        seen = set(state["seen"].get(board, []))
        items = list_archive_items(archive_url)
        todo = sorted((i for i in items if i not in seen), key=int)
        if first_run and len(todo) > MAX_BACKFILL:
            seen.update(todo[:-MAX_BACKFILL])  # skip very old agendas
            todo = todo[-MAX_BACKFILL:]
        print(f"{board}: {len(items)} agendas, processing {len(todo)}")

        for item_id in todo:
            url = items[item_id]
            try:
                text = pdf_text(url)
            except Exception as err:
                print(f"  skip {url}: {err}")
                continue
            date = meeting_date(text)
            for address, desc in extract_cases(text):
                case_id = f"{item_id}:{address.lower()}"
                if case_id in cases:
                    continue
                coords = geocode(address + CITY_SUFFIX, state["geocache"])
                case = {
                    "id": case_id, "board": board, "address": title_case(address),
                    "description": desc, "meeting_date": date, "agenda_url": url,
                    "lat": coords[0] if coords else None,
                    "lon": coords[1] if coords else None, "first_seen": now,
                }
                cases[case_id] = case
                new_cases.append(case)
            seen.add(item_id)
        state["seen"][board] = sorted(seen, key=int)

    ordered = sorted(cases.values(),
                     key=lambda c: (c["meeting_date"] or "", c["address"]), reverse=True)
    save_json(CASES_FILE, {"updated": now, "cases": ordered})
    save_json(STATE_FILE, state)
    print(f"{len(new_cases)} new case(s), {len(ordered)} total")

    if new_cases and not first_run:
        email_digest(new_cases)


if __name__ == "__main__":
    main()
