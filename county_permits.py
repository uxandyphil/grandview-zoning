"""
Building permits from the Franklin County Auditor's monthly public data files.

The auditor publishes "Outside User Files" each month (public domain):
https://apps.franklincountyauditor.com/Outside_User_Files/
The Appraisal set has a Permit table (date, estimated cost, description per
parcel, going back decades) and a Parcel table (site address, property class).

Grandview Heights parcels are tax districts 030 and 035, the first three digits
of the parcel number, so no other lookup is needed to filter them.

Runs from scrape.py. It only downloads when a new monthly file appears, then
spends a limited number of geocoding lookups per run until every address has
a map location. Writes docs/county-permits.json.
"""

import csv
import io
import json
import re
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, unquote

import requests

BASE = "https://apps.franklincountyauditor.com/Outside_User_Files/"
OUT = Path("docs/county-permits.json")
PARCELS_OUT = Path("docs/county-parcels.json")
HISTORY = Path("docs/county-history.json")   # permits from older monthly archives (kept in docs so the workflow commits it)
ARCHIVE_FROM = 2014                      # oldest archive year the county keeps
ARCHIVES_PER_RUN = 3                     # each archive is a large download
DISTRICTS = ("030", "035")          # City of Grandview Heights, Grandview Hts-Columbus
EARLIEST_YEAR = 2005
GEOCODE_PER_RUN = 1200
IMPORT_VERSION = 3   # bump to force a re-import of the current month's file
PARCEL_LINK = "https://audr-apps.franklincountyohio.gov/redir/Link/Parcel/"
HEADERS = {"User-Agent": "grandview-zoning-watch (community site; monthly download)"}

csv.field_size_limit(sys.maxsize)


# ---------- finding the latest file ----------

def links(url):
    html = requests.get(url, headers=HEADERS, timeout=60).text
    return [urljoin(url, h) for h in re.findall(r'href="([^"]+)"', html, re.I)]


def latest_appraisal_folder():
    years = sorted((u for u in links(BASE) if re.search(r"/(20\d\d)/$", u)), key=lambda u: u.rstrip("/")[-4:])
    for year in reversed(years):
        folders = sorted(u for u in links(year) if re.search(r"appraisal/?$", unquote(u), re.I))
        if folders:
            return folders[-1]
    return None


def archive_folders(first_year=2014):
    """The last appraisal folder of each past year, newest year first."""
    out = []
    for year in sorted((u for u in links(BASE) if re.search(r"/(20\d\d)/$", u)), reverse=True):
        y = int(year.rstrip("/")[-4:])
        if y < first_year or y >= datetime.now().year:
            continue
        folders = sorted(u for u in links(year) if re.search(r"appraisal/?$", unquote(u), re.I))
        if folders:
            out.append(folders[-1])
    return out


# ---------- reading tables ----------

def rows_from_tsv(stream):
    text = io.TextIOWrapper(stream, encoding="latin-1", newline="")
    first = text.readline()
    delim = "\t" if "\t" in first else "|" if first.count("|") > first.count(",") else ","
    reader = csv.reader(text, delimiter=delim)
    header = [h.replace("\ufeff", "").replace("ï»¿", "").strip().strip('"') for h in next(csv.reader([first], delimiter=delim))]
    for row in reader:
        yield dict(zip(header, (v.strip() for v in row)))


def rows_from_xlsx(path):
    from openpyxl import load_workbook
    ws = load_workbook(path, read_only=True, data_only=True).active
    it = ws.iter_rows(values_only=True)
    header = [str(h or "").replace("\ufeff", "").strip() for h in next(it)]
    for row in it:
        yield {h: ("" if v is None else v) for h, v in zip(header, row)}


def download(url, tmp):
    path = Path(tmp) / unquote(url.rsplit("/", 1)[-1])
    with requests.get(url, headers=HEADERS, stream=True, timeout=600) as r:
        r.raise_for_status()
        with open(path, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    print(f"  downloaded {path.name} ({path.stat().st_size / 1e6:.0f} MB)")
    return path


class Tables:
    """Opens the Permit and Parcel tables from the zip (preferred) or the .xlsx files."""

    def __init__(self, folder, tmp):
        files = links(folder)
        self.zip = self.xlsx = None
        self.tmp = tmp
        # Newer months have a tab-delimited zip; older archives may only have Excel files, zipped or not
        loose = {Path(unquote(u)).stem.lower(): u for u in files if re.search(r"\.(xlsx|txt|csv)$", u, re.I)}
        tsv = next((u for u in files if re.search(r"tab.?delim.*\.zip$", u, re.I)), None)
        anyzip = next((u for u in files if u.lower().endswith(".zip")), None)
        if tsv or (anyzip and not any(re.fullmatch(r"permits?", k) for k in loose)):
            self.zip = zipfile.ZipFile(download(tsv or anyzip, tmp))
            print("  zip contains:", ", ".join(self.zip.namelist()))
        else:
            self.xlsx = loose
            print("  files:", ", ".join(self.xlsx))

    def rows(self, name):
        if self.zip:
            member = next((m for m in self.zip.namelist()
                           if re.fullmatch(name + r"s?\.(txt|tsv|csv|xlsx)", Path(m).name, re.I)), None)
            if not member:
                raise RuntimeError(f"no {name} table in the zip")
            if member.lower().endswith(".xlsx"):
                yield from rows_from_xlsx(self.zip.extract(member, self.tmp))
            else:
                with self.zip.open(member) as f:
                    yield from rows_from_tsv(f)
        else:
            url = next((u for k, u in self.xlsx.items() if re.fullmatch(name + "s?", k)), None)
            if not url:
                raise RuntimeError(f"no {name} file")
            path = download(url, self.tmp)
            if str(path).lower().endswith(".xlsx"):
                yield from rows_from_xlsx(path)
            else:
                with open(path, "rb") as f:
                    yield from rows_from_tsv(f)


# ---------- columns (names are guessed from the header, then logged) ----------

def find_col(header, *patterns, avoid=()):
    for p in patterns:
        for h in header:
            if re.search(p, h, re.I) and not any(re.search(a, h, re.I) for a in avoid):
                return h
    return None


def parcel_id(v):
    d = re.sub(r"\D", "", str(v or ""))
    return f"{d[:3]}-{d[3:9]}-{d[9:11]}" if len(d) >= 11 else None


def parse_date(v):
    if isinstance(v, datetime):
        return v.date().isoformat()
    s = str(v or "").strip()
    for cand in (s, s.split(" ")[0], s.split("T")[0]):
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%y", "%m/%d/%y", "%d-%b-%y", "%d-%b-%Y", "%b-%d-%Y",
                    "%Y%m%d", "%m/%d/%Y %I:%M:%S %p", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(cand, fmt).date().isoformat()
            except ValueError:
                continue
    m = re.search(r"(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})", s)
    if m:
        y = int(m[3]) + (2000 if len(m[3]) == 2 and int(m[3]) < 50 else 1900 if len(m[3]) == 2 else 0)
        return f"{y}-{int(m[1]):02d}-{int(m[2]):02d}"
    return None


def money(v):
    try:
        return int(round(float(re.sub(r"[^\d.]", "", str(v)) or 0)))
    except ValueError:
        return 0


def title(s):
    return re.sub(r"\s+", " ", str(s or "")).strip()


def load_parcels(tables):
    """{parcel: {address, commercial}} for Grandview parcels."""
    out, header, cols = {}, None, None
    for row in tables.rows("parcel"):
        if header is None:
            header = list(row)
            pc = find_col(header, r"^parcel.?(id|num|no)?$", r"^par.?id$", r"^pin$", r"parcel")
            addr = find_col(header, r"site.?addr", r"prop.*addr", r"^(site|location|property).?address$",
                            avoid=(r"mail", r"owner", r"contact"))
            no = find_col(header, r"^adrno(low)?$", r"house.?(no|num)", r"street.?(no|num)", avoid=(r"mail", r"half", r"high"))
            unit = find_col(header, r"^unit.?no$", r"^unit$")
            dr = find_col(header, r"^adrdir$", r"street.?dir", avoid=(r"mail",))
            st = find_col(header, r"^adrstr$", r"street.?name", avoid=(r"mail",))
            sf = find_col(header, r"^adrsuf$", r"street.?(suf|type)", avoid=(r"mail",))
            cls = find_col(header, r"^class$", r"prop.*class", r"^luc$", r"land.?use")
            luc = find_col(header, r"^luc$", r"land.?use")
            cols = dict(parcel=pc, address=addr, no=no, dir=dr, street=st, suffix=sf, unit=unit, cls=cls, luc=luc)
            print("  parcel columns:", header)
            print("  using:", cols)
        pid = parcel_id(row.get(cols["parcel"]))
        if not pid or pid[:3] not in DISTRICTS:
            continue
        address = title(row.get(cols["address"])) if cols["address"] else ""
        if not address and cols["street"]:
            parts = [str(row.get(cols[k]) or "").strip() for k in ("no", "dir", "street", "suffix") if cols[k]]
            address = title(" ".join(p for p in parts if p and p != "0"))
            u = str(row.get(cols["unit"]) or "").strip() if cols["unit"] else ""
            if address and u and u != "0":
                address += f" UNIT {u}"
        c = str(row.get(cols["cls"]) or "").strip().upper() if cols["cls"] else ""
        out[pid] = {"address": address, "commercial": c.startswith("C") or bool(re.match(r"^[4-6]\d\d$", c)),
                    "cls": c, "luc": str(row.get(cols["luc"]) or "").strip() if cols["luc"] else ""}
    print(f"  {len(out)} Grandview parcels")
    return out


def load_year_built(tables, parcels):
    """Adds year built from the Dwelling table (residential buildings)."""
    header, col_p, col_y, n = None, None, None, 0
    try:
        for row in tables.rows("dwelling"):
            if header is None:
                header = list(row)
                col_p = find_col(header, r"^parcel.?(id|num|no)?$", r"parcel")
                col_y = find_col(header, r"^yrblt$", r"year.?built", r"yr.?blt")
                print("  dwelling columns (year built):", col_y)
                if not col_y:
                    return
            pid = parcel_id(row.get(col_p))
            if pid in parcels and "built" not in parcels[pid]:
                y = re.sub(r"\D", "", str(row.get(col_y) or ""))[:4]
                if y and 1800 < int(y) <= datetime.now().year:
                    parcels[pid]["built"] = int(y)
                    n += 1
    except RuntimeError as err:
        print(f"  no year built ({err})")
    print(f"  year built for {n} properties")


def load_permits(tables, parcels):
    rows, header, cols, samples = [], None, None, 0
    for row in tables.rows("permit"):
        if header is None:
            header = list(row)
            cols = dict(
                parcel=find_col(header, r"^parcel.?(id|num|no)?$", r"^par.?id$", r"^pin$", r"parcel"),
                date=find_col(header, r"^permdt$", r"permit.?(date|dt)", r"issue.?(date|dt)", r"^date$", r"date", r"dt$",
                              avoid=(r"entry", r"upd")),
                ptype=find_col(header, r"permit.?type", r"^type$"),
                cost=find_col(header, r"est.*cost", r"cost", r"amount", r"^amt", r"value"),
                desc=find_col(header, r"desc", r"note", r"remark", r"comment", r"purpose"),
                number=find_col(header, r"permit.?(no|num|id)", r"^permit$", avoid=(r"date",)))
            print("  permit columns:", header)
            print("  using:", cols)
        pid = parcel_id(row.get(cols["parcel"]))
        if not pid or pid[:3] not in DISTRICTS:
            continue
        date = parse_date(row.get(cols["date"])) if cols["date"] else None
        if date and int(date[:4]) < EARLIEST_YEAR:
            continue
        if samples < 3:
            print("  sample row:", {k: row.get(v) for k, v in cols.items() if v})
            samples += 1
        p = parcels.get(pid, {})
        rows.append({"parcel": pid, "date": date, "cost": money(row.get(cols["cost"])) if cols["cost"] else 0,
                     "description": title(row.get(cols["desc"]))[:240] if cols["desc"] else "",
                     "number": title(row.get(cols["number"])) if cols["number"] else "",
                     "permit_type": title(row.get(cols["ptype"])) if cols["ptype"] else "",
                     "address": p.get("address") or "", "commercial": p.get("commercial", False)})
    return rows


def dedupe(rows):
    """The same permit is often listed once per unit or repeated. Keep one per
    address (unit stripped), date and description; keep the largest cost."""
    seen = {}
    for r in rows:
        base = re.sub(r"\s+(UNIT|APT|STE|SUITE|#)\s*\S+$", "", r["address"].upper())
        key = (base or r["parcel"], r["date"], (r.get("number") or r["description"][:80]).upper())
        if key in seen:
            seen[key]["cost"] = max(seen[key]["cost"], r["cost"])
            seen[key]["units"] = seen[key].get("units", 1) + 1
        else:
            seen[key] = dict(r)
    out = list(seen.values())
    for i, r in enumerate(sorted(out, key=lambda r: (r["date"] or "", r["parcel"]))):
        r["id"] = f"FC-{r['parcel']}-{r['date'] or 'nodate'}-{i}"
        r["url"] = PARCEL_LINK + r["parcel"]
    return out


# ---------- main ----------

def backfill(history, current_parcels, max_archives):
    """Reads permits from older monthly archives (each holds about five years) to extend the history."""
    new = []
    try:
        folders = archive_folders(ARCHIVE_FROM)
    except requests.RequestException as err:
        print(f"County archives: couldn't list them ({err})")
        return new
    todo = [f for f in folders if unquote(f.rstrip("/").rsplit("/", 1)[-1]) not in history["done"]]
    if not todo:
        return new
    print(f"County archives: {len(todo)} to go ({ARCHIVES_PER_RUN} per run)")
    for folder in todo[:max_archives]:
        name = unquote(folder.rstrip("/").rsplit("/", 1)[-1])
        print(f"County archive: {name}")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                tables = Tables(folder, tmp)
                try:
                    old = load_parcels(tables)   # addresses as of then, for parcels that no longer exist
                except Exception as err:
                    print(f"  no parcel table ({err})")
                    old = {}
                rows = load_permits(tables, {**old, **current_parcels})
            years = sorted({r["date"][:4] for r in rows if r["date"]})
            print(f"  {len(rows)} Grandview permits" + (f", {years[0]} to {years[-1]}" if years else ""))
            new += rows
            history["done"].append(name)
        except Exception as err:
            fails = history.setdefault("failed", {})
            fails[name] = fails.get(name, 0) + 1
            print(f"  skipped this time ({err})")
            if fails[name] >= 3:
                history["done"].append(name)   # give up on this one after three tries
    if new:
        history["permits"] = dedupe(history["permits"] + new)
    return new


def run(state, geocode, now):
    cs = state.setdefault("county_permits", {})
    history = json.loads(HISTORY.read_text()) if HISTORY.exists() else {"done": [], "permits": []}
    folder = latest_appraisal_folder()
    if not folder:
        print("County permits: couldn't find the auditor's appraisal files")
        return
    name = unquote(folder.rstrip("/").rsplit("/", 1)[-1])
    data = json.loads(OUT.read_text()) if OUT.exists() else None

    if not data or cs.get("folder") != name or cs.get("version") != IMPORT_VERSION:
        print(f"County permits: importing {name}")
        with tempfile.TemporaryDirectory() as tmp:
            tables = Tables(folder, tmp)
            parcels = load_parcels(tables)
            load_year_built(tables, parcels)
            rows = dedupe(load_permits(tables, parcels) + history["permits"])
        old_parcels = {p["parcel"]: p for p in (json.loads(PARCELS_OUT.read_text())["parcels"]
                                                if PARCELS_OUT.exists() else [])}
        plist = []
        for pid, p in sorted(parcels.items()):
            o = old_parcels.get(pid, {})
            plist.append({"parcel": pid, "address": p["address"], "luc": p["luc"], "cls": p["cls"],
                          "built": p.get("built"), "lat": o.get("lat"), "lon": o.get("lon")})
        lucs = {}
        for p in plist:
            lucs[p["luc"]] = lucs.get(p["luc"], 0) + 1
        print("  land use codes:", dict(sorted(lucs.items(), key=lambda kv: -kv[1])[:12]))
        PARCELS_OUT.parent.mkdir(parents=True, exist_ok=True)
        PARCELS_OUT.write_text(json.dumps({"source": name, "updated": now, "parcels": plist}, separators=(",", ":")))
        old = {(p["parcel"], p["date"], p["description"][:80]): p for p in (data or {}).get("permits", [])}
        for r in rows:  # carry map locations over from the last import
            o = old.get((r["parcel"], r["date"], r["description"][:80]))
            if o and o.get("lat") is not None:
                r["lat"], r["lon"] = o["lat"], o["lon"]
        data = {"source": name, "updated": now, "permits": rows}
        cs["folder"], cs["version"] = name, IMPORT_VERSION
        years = sorted({r["date"][:4] for r in rows if r["date"]})
        types = {}
        for r in rows:
            types[r["permit_type"]] = types.get(r["permit_type"], 0) + 1
        print("  permit types:", dict(sorted(types.items(), key=lambda kv: -kv[1])[:15]))
        print(f"  {len(rows)} Grandview permits"
              + (f", {years[0]} to {years[-1]}" if years else "")
              + f", {sum(1 for r in rows if r['cost'])} with a cost")
    else:
        print(f"County permits: {name} already imported")

    # extend the history from older archives, a few per run
    pdata0 = json.loads(PARCELS_OUT.read_text()) if PARCELS_OUT.exists() else {"parcels": []}
    current = {p["parcel"]: {"address": p["address"], "commercial": p.get("cls", "").startswith("C")}
               for p in pdata0["parcels"]}
    added = backfill(history, current, ARCHIVES_PER_RUN)
    if added:
        before = len(data["permits"])
        data["permits"] = dedupe(data["permits"] + added)
        years = sorted({r["date"][:4] for r in data["permits"] if r["date"]})
        print(f"  history now {years[0]} to {years[-1]}: {len(data['permits']) - before} permits added")
    HISTORY.write_text(json.dumps(history, separators=(",", ":")))

    # geocode a batch; addresses repeat a lot, so this goes fast after the first runs
    pending = lambda r: r.get("address") and r.get("lat") is None and not r.get("nomap")
    pdata = json.loads(PARCELS_OUT.read_text()) if PARCELS_OUT.exists() else {"parcels": []}
    todo = [r for r in data["permits"] + pdata["parcels"] if pending(r)]
    cache, lookups = {}, 0
    for r in todo:
        a = re.sub(r"\s+(UNIT|APT|STE|SUITE|#)\s*\S+$", "", r["address"], flags=re.I)  # map the building
        if a not in cache:
            if lookups >= GEOCODE_PER_RUN:
                continue
            cache[a] = geocode(a)
            lookups += 1
        c = cache[a]
        if c:
            r["lat"], r["lon"] = c[0], c[1]
        else:
            r["nomap"] = True
    left = sum(1 for r in data["permits"] + pdata["parcels"] if pending(r))
    if pdata["parcels"]:
        PARCELS_OUT.write_text(json.dumps(pdata, separators=(",", ":")))
    mapped = sum(1 for r in todo if r.get("lat") is not None)
    unmappable = sum(1 for r in data["permits"] if r.get("nomap"))
    print(f"  mapped {mapped} this run, {left} still to map, {unmappable} couldn't be placed")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, separators=(",", ":")))
