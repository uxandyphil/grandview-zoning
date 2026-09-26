"""
Building permits from the city's OpenGov portal.

The portal's front end calls a ViewPoint Cloud search endpoint that looks up
one record number at a time (R-26-82 = prefix R, year 26, sequence 82). There
is no public list endpoint, so we walk each prefix's numbers forward from the
last one we found and stop after a run of misses.

Called from scrape.py once a day. To see raw API responses (useful if the
fields come out blank):

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

import requests

API = os.environ.get("VP_API_BASE", "https://api-east.viewpointcloud.com/v2/grandviewheightsoh")
PORTAL = "https://grandviewheightsoh.portal.opengov.com"
PERMITS_FILE = Path("docs/permits.json")
SEED = os.environ.get("PERMIT_SEED", "R-26-82")  # a number known to exist
# Prefixes to try once a year. Misses cost one tiny request each.
CANDIDATE_PREFIXES = ["R", "B", "C", "M", "Z", "E", "P", "D", "F", "S", "H", "ROW", "SW", "SIGN", "DEMO"]
EXTRA_PREFIXES = [p.strip().upper() for p in os.environ.get("PERMIT_PREFIXES", "").split(",") if p.strip()]
LOOKAHEAD = int(os.environ.get("PERMIT_LOOKAHEAD", "12"))    # misses in a row before we stop
MAX_LOOKUPS = int(os.environ.get("PERMIT_MAX_LOOKUPS", "450"))  # per run, all prefixes
PAUSE_SECONDS = 1.5

HEADERS = {
    "User-Agent": "grandview-zoning-watch/1.1 (community site, runs once daily)",
    "Accept": "application/json, text/plain, */*",
    "Origin": PORTAL,
    "Referer": PORTAL + "/",
}


class Blocked(Exception):
    pass


# ---------- API ----------

def search(key, criteria="record"):
    time.sleep(PAUSE_SECONDS)
    query = urlencode({"criteria": criteria, "key": key,
                       "timeStamp": str(int(time.time() * 1000)), "ignoreCommunity": "true"})
    resp = requests.get(f"{API}/search_results?{query}", headers=HEADERS, timeout=30)
    if resp.status_code in (401, 403, 429):
        raise Blocked(f"HTTP {resp.status_code} from the permit API")
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    try:
        return resp.json()
    except ValueError:
        return None


def norm_number(text):
    """'r-26-0082' -> 'R-26-82', or None if it isn't a record number."""
    m = re.fullmatch(r"\s*([A-Za-z]+)-(\d{2})-0*(\d+)\s*", str(text))
    return f"{m[1].upper()}-{m[2]}-{m[3]}" if m else None


def flatten(obj, prefix=""):
    """Nested JSON -> {'a.b.c': scalar}."""
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


def matching_record(data, number):
    """The smallest dict in the response that contains this exact record number.
    The search may be fuzzy (R-26-8 could match R-26-80), so exact match only."""
    best = None

    def walk(node, parent):
        nonlocal best
        if isinstance(node, dict):
            if any(isinstance(v, str) and norm_number(v) == number for v in node.values()):
                # keep the parent's scalar fields too; they often hold the address
                merged = {k: v for k, v in (parent or {}).items() if not isinstance(v, (dict, list))}
                merged.update(node)
                if best is None or len(json.dumps(merged)) < len(json.dumps(best)):
                    best = merged
            for v in node.values():
                walk(v, node)
        elif isinstance(node, list):
            for v in node:
                walk(v, parent)

    walk(data, None)
    return best


def pick(flat, key_pattern, value_test=lambda v: True, skip=()):
    for k, v in flat.items():
        leaf = k.rsplit(".", 1)[-1]
        if re.search(key_pattern, leaf, re.I) and not any(re.search(s, leaf, re.I) for s in skip) \
                and value_test(v):
            return v
    return None


def looks_like_address(v):
    return isinstance(v, str) and re.match(r"\s*\d{1,5}\s+[A-Za-z]", v) and len(v) < 120


def parse_when(v):
    if isinstance(v, (int, float)) and v > 1e11:  # epoch ms
        return datetime.fromtimestamp(v / 1000, timezone.utc).date().isoformat()
    if isinstance(v, str) and (m := re.match(r"(20\d\d-\d\d-\d\d)", v)):
        return m[1]
    return None


def to_permit(number, rec):
    flat = flatten(rec)
    is_number = lambda v: isinstance(v, str) and norm_number(v) == number
    address = pick(flat, r"address|location|street|secondary|subtitle|description", looks_like_address) \
        or next((v for v in flat.values() if looks_like_address(v)), None)
    type_ok = lambda v: isinstance(v, str) and not is_number(v) and not looks_like_address(v) \
        and 2 < len(v) < 120 and v.lower() not in ("record", "records")
    rtype = next((t for pattern in (r"recordType|typeName|template|permitType|category",
                                    r"type|name|title|resultText")
                  if (t := pick(flat, pattern, type_ok, skip=(r"^resulttype$", r"criteria")))), None)
    status = pick(flat, r"status", lambda v: isinstance(v, str))
    date = next((d for d in (parse_when(v) for k, v in flat.items()
                             if re.search(r"submit|creat|appl|issue|date", k, re.I)) if d), None)
    rec_id = pick(flat, r"^(entityID|recordID|record_id|id)$",
                  lambda v: isinstance(v, (int, str)) and not is_number(v))
    desc = pick(flat, r"desc|summary|scope|work",
                lambda v: isinstance(v, str) and not looks_like_address(v) and len(v) > 3)
    if address:
        address = re.sub(r",?\s*(Grandview Heights|Columbus)?,?\s*OH(io)?\b.*$", "", address, flags=re.I).strip(" ,")
    return {"number": number, "type": rtype, "status": status, "address": address,
            "description": desc, "date": date,
            "url": f"{PORTAL}/records/{rec_id}" if rec_id else f"{PORTAL}/search"}


def lookup(number):
    data = search(number)
    rec = matching_record(data, number) if data else None
    return (to_permit(number, rec), rec) if rec else (None, None)


# ---------- walking ----------

def discover_prefixes(year, budget):
    """Which prefixes have records this year? Try sequence 1-3 for each candidate."""
    seed_prefix = norm_number(SEED).split("-")[0] if norm_number(SEED) else "R"
    found = []
    for prefix in dict.fromkeys([seed_prefix] + EXTRA_PREFIXES + CANDIDATE_PREFIXES):
        for seq in (1, 2, 3):
            if budget[0] <= 0:
                return found
            budget[0] -= 1
            if lookup(f"{prefix}-{year}-{seq}")[0]:
                found.append(prefix)
                break
    if seed_prefix not in found and SEED.split("-")[1:2] == [year]:
        found.insert(0, seed_prefix)  # seed proves it exists even if 1-3 were withdrawn
    print(f"Permit prefixes for 20{year}: {found or 'none found'}")
    return found


def run(state, geocode, now):
    """Update docs/permits.json. geocode(address) -> [lat, lon] or None."""
    ps = state.setdefault("permits", {"cursors": {}, "prefixes": {}})
    existing = json.loads(PERMITS_FILE.read_text()) if PERMITS_FILE.exists() else {"permits": []}
    permits = {p["number"]: p for p in existing.get("permits", [])}
    first_run = not permits
    budget = [MAX_LOOKUPS]
    new, logged_sample = [], False

    today = datetime.now(timezone.utc)
    years = [today.strftime("%y")] + ([f"{(today.year - 1) % 100:02d}"] if today.month == 1 else [])
    try:
        for year in years:
            if year not in ps["prefixes"]:
                ps["prefixes"][year] = discover_prefixes(year, budget)
            for prefix in ps["prefixes"][year]:
                series = f"{prefix}-{year}"
                n = ps["cursors"].get(series, 0) + 1
                misses = 0
                while misses < LOOKAHEAD and budget[0] > 0:
                    number = f"{series}-{n}"
                    budget[0] -= 1
                    permit, raw = lookup(number) if number not in permits else (permits[number], None)
                    if permit:
                        if raw and not logged_sample:
                            print("Sample API record (for debugging field names):")
                            print(json.dumps(raw, indent=1)[:1500])
                            logged_sample = True
                        if number not in permits:
                            coords = geocode(permit["address"]) if permit["address"] else None
                            permit.update(lat=coords[0] if coords else None,
                                          lon=coords[1] if coords else None, first_seen=now)
                            permits[number] = permit
                            new.append(permit)
                        ps["cursors"][series] = n
                        misses = 0
                    else:
                        misses += 1
                    n += 1
                print(f"Permits {series}: up to #{ps['cursors'].get(series, 0)}")
    except Blocked as err:
        print(f"Permit API refused the request ({err}). Keeping existing permits.json.")
    except requests.RequestException as err:
        print(f"Permit API error ({err}). Saving what we have.")

    if budget[0] <= 0:
        print("Hit PERMIT_MAX_LOOKUPS; the next run continues where this one stopped.")
    ordered = sorted(permits.values(), key=lambda p: (p.get("date") or p["first_seen"][:10], p["number"]),
                     reverse=True)
    PERMITS_FILE.parent.mkdir(parents=True, exist_ok=True)
    PERMITS_FILE.write_text(json.dumps({"updated": now, "permits": ordered}, indent=2, sort_keys=True))
    print(f"{len(new)} new permit(s), {len(ordered)} total")
    return [] if first_run else new


# ---------- probe ----------

def probe():
    """Print raw responses so we can see what the API returns."""
    for label, key, criteria in [("seed record", SEED, "record"),
                                 ("record prefix search", SEED.rsplit("-", 1)[0] + "-", "record"),
                                 ("address search", "1st Ave", "location")]:
        print("=" * 70, f"\n{label}: criteria={criteria} key={key}")
        try:
            data = search(key, criteria)
            print(json.dumps(data, indent=1)[:3000])
            if criteria == "record" and data:
                rec = matching_record(data, norm_number(key) or "")
                print("\nParsed:", to_permit(norm_number(key), rec) if rec else "no exact match")
        except Exception as err:
            print("error:", err)


if __name__ == "__main__":
    if "--probe" in sys.argv:
        probe()
    else:
        print("Run scrape.py for the full update, or permits.py --probe to inspect the API.")
