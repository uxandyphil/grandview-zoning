"""
Building permits from the city's OpenGov portal.

The portal's search endpoint (ViewPoint Cloud) takes a record number like
R-26-82 and returns up to ~20 fuzzy matches, each with the record number,
its type ("Building Permit - Residential") and an internal id, but no
address. So:

  1. Walk each series (R-26, E-26, ZON-26, ...) forward from the last number
     found. Every search also returns neighbors, so most numbers come free.
  2. For each new record, open its portal page once to read the address and
     filing date.

The API refuses plain scripted requests (HTTP 403), so everything runs in a
headless browser that loads the portal like a visitor and reuses the headers
the page itself sends. Called from scrape.py once a day. To inspect:

    python permits.py --probe
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

API = os.environ.get("VP_API_BASE", "https://api-east.viewpointcloud.com/v2/grandviewheightsoh")
PORTAL = "https://grandviewheightsoh.portal.opengov.com"
PERMITS_FILE = Path("docs/permits.json")
SEED = os.environ.get("PERMIT_SEED", "R-26-82")
# Prefixes seen on the portal so far, plus a few guesses. Checked once a year.
CANDIDATE_PREFIXES = ["R", "E", "H", "P", "ZON", "B", "C", "D", "F", "M", "S", "Z", "ROW", "SIGN", "DEMO"]
EXTRA_PREFIXES = [p.strip().upper() for p in os.environ.get("PERMIT_PREFIXES", "").split(",") if p.strip()]
LOOKAHEAD = int(os.environ.get("PERMIT_LOOKAHEAD", "12"))        # misses in a row before a series stops
MAX_SEARCHES = int(os.environ.get("PERMIT_MAX_SEARCHES", "300"))  # API searches per run
MAX_DETAILS = int(os.environ.get("PERMIT_MAX_DETAILS", "120"))    # record pages opened per run
PAUSE_SECONDS = 1.5
GIVE_UP_AFTER = 3  # refused runs in a row before the watcher turns itself off

NUMBER_RE = re.compile(r"\b([A-Z]{1,5})-(\d{2})-0*(\d{1,6})\b", re.I)
STREET = (r"(?:Ave(?:nue)?|St(?:reet)?|Rd|Road|Blvd|Boulevard|Dr(?:ive)?|Ct|Court|Pl(?:ace)?|Way|"
          r"Ln|Lane|Pkwy|Parkway|Cir(?:cle)?|Ter(?:race)?)")
ADDRESS_RE = re.compile(r"\b\d{1,5}(?:-\d{1,5})?\s+(?:[NSEW]\.?\s+)?(?:[A-Za-z0-9']+\.?\s+){1,3}?"
                        + STREET + r"\b\.?", re.I)
CITY_HALL = re.compile(r"^1525\s+(W\.?\s+)?Goodale", re.I)


class Blocked(Exception):
    pass


def norm_number(text):
    """'Record r-26-0082' -> 'R-26-82', or None."""
    m = NUMBER_RE.search(str(text or ""))
    return f"{m[1].upper()}-{m[2]}-{int(m[3])}" if m else None


def seq_of(number):
    return int(number.rsplit("-", 1)[1])


# ---------- browser session ----------

_b = {}
# Headers a page script can't set (the browser adds its own) or shouldn't copy
BROWSER_HEADERS = {"host", "content-length", "cookie", "connection", "accept-encoding", "user-agent",
                   "origin", "referer", "priority", "if-none-match", "if-modified-since"}


def open_browser():
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    browser = pw.chromium.launch()
    context = browser.new_context()
    page = context.new_page()
    seen, statuses = [], []
    page.on("request", lambda r: seen.append(r) if "viewpointcloud.com" in r.url else None)
    page.on("response", lambda r: statuses.append(r.status) if "search_results" in r.url else None)
    page.goto(PORTAL + "/search", wait_until="networkidle", timeout=90_000)
    page.wait_for_timeout(2000)
    print(f"Portal page loaded: '{page.title()}'")
    try:  # run one search so the page makes an API call we can copy
        box = page.locator("input[type=search], input[type=text]").first
        box.fill(SEED, timeout=15_000)
        box.press("Enter")
        page.wait_for_timeout(6000)
    except Exception as err:
        print(f"  couldn't use the search box ({err})")
    headers = {}
    if seen:
        headers = {k: v for k, v in seen[-1].all_headers().items()
                   if k.lower() not in BROWSER_HEADERS and not k.startswith((":", "sec-"))}
        print(f"  page made {len(seen)} API call(s); copying headers: {', '.join(sorted(headers)) or 'none'}")
    else:
        print("  the page made no API calls we could see")
    print(f"  the page's own searches got HTTP {statuses or 'no response'}")
    _b.update(pw=pw, browser=browser, context=context, page=page, headers=headers)


def close_browser():
    if _b:
        _b["browser"].close()
        _b["pw"].stop()
        _b.clear()


def search(key, criteria="record"):
    if not _b:
        open_browser()
    time.sleep(PAUSE_SECONDS)
    query = urlencode({"criteria": criteria, "key": key,
                       "timeStamp": str(int(time.time() * 1000)), "ignoreCommunity": "true"})
    # Run the request inside the portal page itself, exactly like the site's own search does
    resp = _b["page"].evaluate("""async ([url, headers]) => {
        const r = await fetch(url, { headers, credentials: "include", cache: "no-store" });
        return { status: r.status, body: await r.text() };
    }""", [f"{API}/search_results?{query}", _b["headers"]])
    if resp["status"] in (401, 403, 429):
        raise Blocked(f"HTTP {resp['status']} from the permit API")
    if resp["status"] >= 400:
        raise RuntimeError(f"HTTP {resp['status']} from the permit API")
    try:
        return json.loads(resp["body"])
    except ValueError:
        return None


# ---------- search results ----------

def harvest(data, pool):
    """Add every record in a search response to pool: {number: {type, id}}."""
    items = data if isinstance(data, list) else (data or {}).get("results") or (data or {}).get("value") or []
    for item in items:
        if not isinstance(item, dict) or item.get("entityType", "record") != "record":
            continue
        number = norm_number(item.get("resultText"))
        if number:
            pool.setdefault(number, {"type": (item.get("secondaryText") or "").strip() or None,
                                     "id": item.get("entityID")})


class Finder:
    """Answers 'does this record number exist?', using as few searches as possible."""

    def __init__(self, budget):
        self.pool, self.searched, self.budget = {}, set(), budget

    def get(self, number):
        if number not in self.pool and number not in self.searched:
            if self.budget <= 0:
                raise StopIteration
            self.budget -= 1
            self.searched.add(number)
            harvest(search(number), self.pool)
        return self.pool.get(number)


# ---------- record page (address, date) ----------

def flatten(obj, prefix=""):
    out = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.update(flatten(v, f"{prefix}{k}."))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.update(flatten(v, f"{prefix}{i}."))
    elif obj not in (None, ""):
        out[prefix.rstrip(".")] = obj
    return out


def clean_address(a):
    a = re.sub(r"\s+", " ", a).strip(" ,.")
    return re.sub(r",?\s*(Grandview Heights|Columbus)?,?\s*OH(io)?\b.*$", "", a, flags=re.I).strip(" ,")


def parse_date(v):
    if isinstance(v, (int, float)) and v > 1e11:
        return datetime.fromtimestamp(v / 1000, timezone.utc).date().isoformat()
    if isinstance(v, str):
        if m := re.search(r"(20\d\d-\d\d-\d\d)", v):
            return m[1]
        if m := re.search(r"\b(\d{1,2})/(\d{1,2})/(20\d\d)\b", v):
            return f"{m[3]}-{int(m[1]):02d}-{int(m[2]):02d}"
    return None


def record_details(entity_id, number):
    """Open the record's portal page; return (address, date, sample_json)."""
    page = _b["page"]
    responses = []
    handler = lambda r: responses.append(r) if "viewpointcloud.com" in r.url else None
    page.on("response", handler)
    try:
        time.sleep(PAUSE_SECONDS)
        page.goto(f"{PORTAL}/records/{entity_id}", wait_until="networkidle", timeout=60_000)
        page.wait_for_timeout(1500)
        text = page.inner_text("body")
    finally:
        page.remove_listener("response", handler)

    address = date = sample = None
    # 1. Structured data the page loaded
    for r in responses:
        try:
            data = r.json()
        except Exception:
            continue
        flat = flatten(data)
        blob = json.dumps(data)
        if number.split("-")[-1] not in blob and str(entity_id) not in blob:
            continue
        sample = sample or data
        for k, v in flat.items():
            leaf = k.rsplit(".", 1)[-1].lower()
            if not address and isinstance(v, str) and re.search(r"address|location|street", leaf) \
                    and ADDRESS_RE.search(v) and not CITY_HALL.search(v):
                address = clean_address(ADDRESS_RE.search(v).group(0))
            if not date and re.search(r"submit|creat|applied|dateopen|issued", leaf):
                date = parse_date(v)
        if not address:  # streetNo + streetName style
            no = next((v for k, v in flat.items() if re.search(r"street(no|number)|houseno", k, re.I)), None)
            name = next((v for k, v in flat.items() if re.search(r"streetname", k, re.I)), None)
            if no and name:
                address = clean_address(f"{no} {name}")
    # 2. Fall back to the visible page text
    if not address:
        label = re.search(r"(Location|Address|Property)\s*:?\s*\n?", text, re.I)
        region = text[label.end():label.end() + 300] if label else text
        m = next((m for m in ADDRESS_RE.finditer(region) if not CITY_HALL.search(m.group(0))), None)
        address = clean_address(m.group(0)) if m else None
    if not date:
        m = re.search(r"(Submitted|Applied|Created|Date)\D{0,20}(\d{1,2}/\d{1,2}/20\d\d)", text, re.I)
        date = parse_date(m.group(2)) if m else None
    return address, date, sample


# ---------- main ----------

def discover_prefixes(year, finder):
    found = []
    seed = norm_number(SEED)
    for prefix in dict.fromkeys(([seed.split("-")[0]] if seed else []) + EXTRA_PREFIXES + CANDIDATE_PREFIXES):
        if any(n.startswith(f"{prefix}-{year}-") for n in finder.pool):
            found.append(prefix)  # already showed up as a neighbor in an earlier search
            continue
        for seq in (1, 2, 3):
            if finder.get(f"{prefix}-{year}-{seq}"):
                found.append(prefix)
                break
    print(f"Permit series for 20{year}: {', '.join(found) or 'none found'}")
    return found


def run(state, geocode, now):
    """Update docs/permits.json. geocode(address) -> [lat, lon] or None."""
    ps = state.setdefault("permits", {})
    ps.setdefault("cursors", {})
    ps.setdefault("prefixes", {})
    refused = ps.get("refused_runs", 0)
    if refused >= GIVE_UP_AFTER:
        print(f"Permits: skipped. The API refused the last {refused} runs, so the watcher turned "
              'itself off. To try again, set "refused_runs" to 0 in state.json.')
        return []

    existing = json.loads(PERMITS_FILE.read_text()) if PERMITS_FILE.exists() else {"permits": []}
    permits = {p["number"]: p for p in existing.get("permits", [])}
    first_run = not permits
    finder, new = Finder(MAX_SEARCHES), []
    today = datetime.now(timezone.utc)
    years = [today.strftime("%y")] + ([f"{(today.year - 1) % 100:02d}"] if today.month == 1 else [])

    try:
        # 1. find new record numbers
        try:
            for year in years:
                if year not in ps["prefixes"]:
                    ps["prefixes"][year] = discover_prefixes(year, finder)
                for prefix in ps["prefixes"][year]:
                    series = f"{prefix}-{year}"
                    n, misses = ps["cursors"].get(series, 0) + 1, 0
                    while misses < LOOKAHEAD:
                        number = f"{series}-{n}"
                        hit = permits.get(number) or finder.get(number)
                        if hit:
                            if number not in permits:
                                permits[number] = {
                                    "number": number, "type": hit["type"], "entity_id": hit["id"],
                                    "url": f"{PORTAL}/records/{hit['id']}" if hit["id"] else f"{PORTAL}/search",
                                    "address": None, "date": None, "lat": None, "lon": None,
                                    "first_seen": now, "details": False}
                                new.append(permits[number])
                            ps["cursors"][series] = n
                            misses = 0
                        else:
                            misses += 1
                        n += 1
                    print(f"Permits {series}: up to #{ps['cursors'].get(series, 0)}")
        except StopIteration:
            print(f"Hit {MAX_SEARCHES} searches; the next run picks up where this one stopped.")

        # 2. fill in address and date, newest first, a limited number per run
        todo = sorted((p for p in permits.values() if not p.get("details") and p.get("entity_id")),
                      key=lambda p: p["entity_id"], reverse=True)[:MAX_DETAILS]
        shown_sample = False
        for p in todo:
            try:
                address, date, sample = record_details(p["entity_id"], p["number"])
            except Exception as err:
                print(f"  {p['number']}: record page failed ({err})")
                continue
            if sample and not shown_sample:
                print(f"Sample record data for {p['number']} (for debugging field names):")
                print(json.dumps(sample, indent=1)[:2000])
                shown_sample = True
            p.update(address=address, date=date or p["date"], details=True)
            if address:
                coords = geocode(address)
                p.update(lat=coords[0] if coords else None, lon=coords[1] if coords else None)
        left = sum(1 for p in permits.values() if not p.get("details"))
        print(f"Permit details: {len(todo)} looked up, {left} still to do"
              f"{', ' + str(sum(1 for p in todo if not p['address'])) + ' without an address' if todo else ''}")
        ps["refused_runs"] = 0
    except Blocked as err:
        ps["refused_runs"] = refused + 1
        print(f"Permit API refused the request ({err}), even through the browser. "
              f"Refused {ps['refused_runs']} run(s) in a row; stops after {GIVE_UP_AFTER}.")
    except Exception as err:
        print(f"Permit step error ({err}). Saving what we have.")
    finally:
        close_browser()

    ordered = sorted(permits.values(),
                     key=lambda p: (p.get("date") or p["first_seen"][:10], p.get("entity_id") or 0),
                     reverse=True)
    PERMITS_FILE.parent.mkdir(parents=True, exist_ok=True)
    PERMITS_FILE.write_text(json.dumps({"updated": now, "permits": ordered}, indent=2, sort_keys=True))
    print(f"{len(new)} new permit(s), {len(ordered)} total")
    return [] if first_run else new


def probe():
    try:
        data = search(SEED)
        print(json.dumps(data, indent=1)[:1500])
        pool = {}
        harvest(data, pool)
        print(f"\nParsed {len(pool)} records from one search:", dict(list(pool.items())[:5]))
        hit = pool.get(norm_number(SEED))
        if hit:
            print("\nRecord page:", record_details(hit["id"], norm_number(SEED))[:2])
    finally:
        close_browser()


if __name__ == "__main__":
    if "--probe" in sys.argv:
        probe()
    else:
        print("Run scrape.py for the full update, or permits.py --probe to inspect the API.")
