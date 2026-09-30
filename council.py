"""
City Council agendas, summarized.

Grandview Heights posts meeting agendas on CivicClerk (grandviewheightsoh.portal.civicclerk.com),
whose public portal is backed by a public data service. Each run lists recent City Council
meetings (and council committees), reads each agenda, and pulls out its items: ordinances,
resolutions, public hearings, and so on, with their titles. The summary is built from the agenda's
own wording, not written by a model. Writes docs/council.json. Runs from scrape.py.
"""

import io
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

TENANT = "grandviewheightsoh"
API = f"https://{TENANT}.api.civicclerk.com/v1/"
PORTAL = f"https://{TENANT}.portal.civicclerk.com/event/"
OUT = Path("docs/council.json")
UA = "grandview-zoning-watch/1.1 (community site, runs once daily)"
COUNCIL = re.compile(r"council", re.I)
DAYS_BACK, MAX_NEW_PER_RUN = 540, 25
KINDS = [
    ("Ordinance", re.compile(r"\bord(inance)?\b\.?\s*(no\.?\s*)?\d|\bAN ORDINANCE\b|^ordinance", re.I)),
    ("Resolution", re.compile(r"\bres(olution)?\b\.?\s*(no\.?\s*)?\d|\bA RESOLUTION\b|^resolution", re.I)),
    ("Public hearing", re.compile(r"public hearing", re.I)),
    ("Proclamation", re.compile(r"proclamation", re.I)),
    ("Appointment", re.compile(r"appoint|reappoint|confirm", re.I)),
    ("Presentation", re.compile(r"presentation|recognition|report from|update from", re.I)),
]
# Routine agenda headings that aren't business items
ROUTINE = re.compile(r"^(adjourn\w*|public hearings?|ordinances|resolutions|proclamations|presentations|appointments|call to order|roll call|pledge|invocation|approval of (the )?(minutes|agenda)|minutes|adjourn|"
                     r"executive session|citizens?(\s+comments?)?|public comments?|visitors|reports?|old business|new business|"
                     r"first reading|second reading|third reading|council (member )?comments|mayor'?s report|"
                     r"director'?s reports?|other business|communications|consent agenda)\s*:?$", re.I)


def get(path, **params):
    r = requests.get(API + path, params=params, headers={"User-Agent": UA, "Accept": "application/json"}, timeout=60)
    r.raise_for_status()
    return r


def events():
    """Recent and upcoming council meetings, newest first."""
    since = (datetime.now(timezone.utc) - timedelta(days=DAYS_BACK)).strftime("%Y-%m-%dT00:00:00Z")
    out, url, params = [], "Events", {"$filter": f"startDateTime gt {since}", "$orderby": "startDateTime desc", "$top": 100}
    while url and len(out) < 400:
        data = (get(url, **params) if params else requests.get(url, headers={"User-Agent": UA}, timeout=60)).json()
        out += data.get("value", data if isinstance(data, list) else [])
        url, params = data.get("@odata.nextLink") if isinstance(data, dict) else None, None
    if out:
        print("Council: event fields:", sorted(out[0].keys()))
    return [e for e in out if COUNCIL.search(" ".join(str(e.get(k) or "") for k in ("eventName", "categoryName", "name")))]


def classify(title):
    for kind, pat in KINDS:
        if pat.search(title):
            return kind
    return "Other"


def clean(t):
    t = re.sub(r"<[^>]+>", " ", str(t or ""))
    return re.sub(r"\s+", " ", t).strip(" .:-")


def items_from_meeting(agenda_id):
    """Agenda items from the structured agenda, if the meeting has one."""
    m = get(f"Meetings/{agenda_id}").json()
    found = []
    def walk(node, depth=0):
        if isinstance(node, dict):
            title = clean(node.get("agendaObjectItemName") or node.get("itemName") or node.get("name") or node.get("title"))
            number = clean(node.get("agendaObjectItemOutlineNumber") or node.get("outlineNumber") or node.get("number"))
            if title and not ROUTINE.match(title) and len(title) > 3 and depth > 0:
                found.append({"number": number, "title": title[:400]})
            for k in ("items", "childItems", "children", "agendaItems", "agendaObjectItems"):
                for c in node.get(k) or []:
                    walk(c, depth + 1)
        elif isinstance(node, list):
            for c in node:
                walk(c, depth + 1)
    walk(m)
    return found


def items_from_text(text):
    """Agenda items from the agenda's plain text: numbered or lettered lines, and ORDINANCE / RESOLUTION blocks."""
    lines = [clean(l) for l in text.split("\n")]
    found, cur = [], None
    for line in lines:
        if not line:
            continue
        m = re.match(r"^((?:[IVX]+|\d+|[A-Z])[\.\)])\s+(.*)", line) or re.match(r"^((?:ORD|RES)(?:INANCE|OLUTION)?[\s\.#-]*(?:NO\.?\s*)?\d[\d-]*)\s*[:\-]?\s*(.*)", line, re.I)
        if m:
            cur = {"number": m[1].strip(), "title": m[2].strip()}
            found.append(cur)
        elif cur and len(cur["title"]) < 400 and not re.match(r"^(page \d|agenda|grandview heights)", line, re.I):
            cur["title"] += " " + line
    return [f for f in found if f["title"] and not ROUTINE.match(f["title"]) and len(f["title"]) > 3]


def agenda_file(event):
    files = event.get("publishedFiles") or event.get("files") or []
    return next((f for f in files if re.search(r"agenda", f"{f.get('type', '')} {f.get('name', '')}", re.I)
                 and not re.search(r"minutes", str(f.get("name", "")), re.I)), None)


def agenda_text(file_id):
    r = get(f"Meetings/GetMeetingFileStream(fileId={file_id},plainText=true)")
    if r.content.startswith(b"%PDF"):
        from pypdf import PdfReader
        return "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(r.content)).pages)
    try:
        return r.json() if isinstance(r.json(), str) else r.text
    except ValueError:
        return r.text


def summarize(items):
    counts = {}
    for it in items:
        counts[it["kind"]] = counts.get(it["kind"], 0) + 1
    return counts


def run(state, now):
    data = json.loads(OUT.read_text()) if OUT.exists() else {"meetings": []}
    try:
        evs = events()
    except Exception as err:
        print(f"Council: couldn't list meetings ({err})")
        return
    have = {m["id"]: m for m in data["meetings"]}
    todo, done = [], 0
    for e in evs:
        eid = e.get("id")
        f = agenda_file(e)
        key = f"{eid}:{f and f.get('fileId')}:{e.get('agendaId')}"
        if eid in have and have[eid].get("key") == key and have[eid].get("items"):
            continue
        todo.append((e, f, key))
    print(f"Council: {len(evs)} council meetings, {len(todo)} to read")
    for e, f, key in todo[:MAX_NEW_PER_RUN]:
        items, source = [], None
        try:
            if e.get("agendaId"):
                items, source = items_from_meeting(e["agendaId"]), "structured"
        except Exception as err:
            print(f"  {e.get('id')}: structured agenda failed ({err})")
        if not items and f:
            try:
                items, source = items_from_text(agenda_text(f.get("fileId"))), "text"
            except Exception as err:
                print(f"  {e.get('id')}: agenda file failed ({err})")
        for it in items:
            it["kind"] = classify(f"{it['number']} {it['title']}")
        start = e.get("startDateTime") or e.get("eventDate") or ""
        have[e.get("id")] = {"id": e.get("id"), "key": key, "name": clean(e.get("eventName") or e.get("name")),
                             "category": clean(e.get("categoryName")), "date": start[:10], "time": start[11:16],
                             "url": PORTAL + str(e.get("id")) + "/files", "items": items, "counts": summarize(items),
                             "source": source, "has_agenda": bool(f or e.get("agendaId"))}
        done += 1
        print(f"  {start[:10]} {e.get('eventName')}: {len(items)} items ({source})")
    data["meetings"] = sorted(have.values(), key=lambda m: m["date"], reverse=True)
    data["updated"] = now
    OUT.write_text(json.dumps(data, indent=1))
    print(f"Council: {len(data['meetings'])} meetings on file, {done} read this run")
