"""
City Council agendas, summarized.

The city posts council agendas in its Archive Center ("City Council Minutes and Agendas" on the
Public Documents page), the same system the zoning board agendas come from. The archives are
numbered and the index doesn't list them, so the first run checks the numbers once and remembers
which hold council agendas. Each run then reads new agenda PDFs and pulls out the items:
ordinances, resolutions, public hearings, and so on, in the agenda's own words (no model writes the
summary). Upcoming council meetings from the city calendar's RSS feed are listed too, until their
agenda is posted.
Meetings are kept once read, so the history builds up. Writes docs/council.json. Runs from scrape.py.
"""

import json
import re
import time
from html import unescape
from pathlib import Path
from urllib.parse import urljoin

import requests

BASE = "https://www.grandviewheights.gov/"
FEEDS = [BASE + "RSSFeed.aspx?ModID=58&CID=All-calendar.xml"]
MAX_ARCHIVE_ID = 120
DAYS_BACK = 550
OUT = Path("docs/council.json")
UA = "grandview-zoning-watch/1.1 (community site, runs once daily)"
COUNCIL = re.compile(r"council", re.I)
MAX_NEW_PER_RUN = 25
KINDS = [
    ("Ordinance", re.compile(r"\bord(inance)?\b\.?\s*(no\.?\s*)?\d|\bAN ORDINANCE\b|^ordinance", re.I)),
    ("Resolution", re.compile(r"\bres(olution)?\b\.?\s*(no\.?\s*)?\d|\bA RESOLUTION\b|^resolution", re.I)),
    ("Public hearing", re.compile(r"public hearing", re.I)),
    ("Proclamation", re.compile(r"proclamation", re.I)),
    ("Appointment", re.compile(r"appoint|reappoint|confirm", re.I)),
    ("Presentation", re.compile(r"presentation|recognition|report from|update from", re.I)),
]
# Routine agenda headings that aren't business items
ROUTINE = re.compile(r"^(adjourn\w*|public hearings?|ordinances|resolutions|proclamations|presentations|appointments|call to order|"
                     r"roll call|pledge|invocation|approval of (the )?(minutes|agenda)|minutes|adjourn|"
                     r"executive session|citizens?(\s+comments?)?|public comments?|visitors|reports?|old business|new business|"
                     r"first reading|second reading|third reading|council (member )?comments|mayor'?s report|"
                     r"director'?s reports?|other business|communications|consent agenda)\s*:?$", re.I)


def get(url):
    r = requests.get(url, headers={"User-Agent": UA}, timeout=60)
    r.raise_for_status()
    return r


def meetings_from_feed():
    """[{id, name, date, url}] for council meetings in the calendar feed."""
    out = {}
    for feed in FEEDS:
        xml = get(feed).text
        for item in re.findall(r"<item>(.*?)</item>", xml, re.S):
            title = unescape(re.sub(r"<[^>]+>", "", (re.search(r"<title>(.*?)</title>", item, re.S) or [None, ""])[1])).strip()
            link = unescape((re.search(r"<link>(.*?)</link>", item, re.S) or [None, ""])[1]).strip()
            eid = (re.search(r"EID=(\d+)", link) or [None, None])[1]
            if not eid or not COUNCIL.search(title):
                continue
            desc = unescape(unescape((re.search(r"<description>(.*?)</description>", item, re.S) or [None, ""])[1]))
            when = re.search(r"Event date:\s*</strong>\s*([A-Za-z]+ \d{1,2}, \d{4})", desc) or re.search(r"([A-Z][a-z]+ \d{1,2}, \d{4})", desc)
            tm = re.search(r"Event Time:\s*</strong>\s*(\d{1,2}:\d{2} [AP]M)", desc)
            out[eid] = {"id": eid, "name": title, "date_text": when[1] if when else "", "time_text": tm[1] if tm else "",
                        "url": BASE + f"Calendar.aspx?EID={eid}"}
    return list(out.values())


def classify(title):
    for kind, pat in KINDS:
        if pat.search(title):
            return kind
    return "Other"


def clean(t):
    t = re.sub(r"<[^>]+>", " ", str(t or ""))
    return re.sub(r"\s+", " ", t).strip(" .:-")


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


def summarize(items):
    counts = {}
    for it in items:
        counts[it["kind"]] = counts.get(it["kind"], 0) + 1
    return counts


def archive_items(html):
    """[(title, url)] for the documents listed on an Archive Center page."""
    return [(re.sub(r"<[^>]+>|\s+", " ", unescape(t)).strip(), urljoin(BASE, unescape(h)))
            for h, t in re.findall(r'<a\b[^>]*href="([^"]*ADID=\d+[^"]*)"[^>]*>(.*?)</a>', html, re.I | re.S)]


def archive_name(html):
    """The archive's own name: the last heading-like text before its first document."""
    first = re.search(r'href="[^"]*ADID=', html)
    head = html[:first.start()] if first else html
    names = [unescape(re.sub(r"\s+", " ", t)).strip() for _, t in re.findall(r"<(h[1-4]|strong|span|legend)[^>]*>([^<]{3,90})</\1>", head, re.I)]
    names = [n for n in names if not re.search(r"archive center|skip to|sign in|search|loading|menu", n, re.I)]
    return names[-1] if names else ""


def council_archives(state):
    """{archive id: name} for Archive Center archives of council agendas (found once, then remembered)."""
    if state.get("council_archives"):
        return state["council_archives"]
    found = {}
    for amid in range(1, MAX_ARCHIVE_ID + 1):
        try:
            html = get(f"{BASE}Archive.aspx?AMID={amid}").text
        except requests.RequestException:
            continue
        items = archive_items(html)
        if items:
            found[str(amid)] = archive_name(html) or items[0][0]
        time.sleep(0.4)
    print(f"Council: archives found {found}")
    council = {a: n for a, n in found.items() if COUNCIL.search(n) and not re.search(r"minutes|committee of the whole minutes", n, re.I)}
    if council:
        state["council_archives"] = council
    return council


def run(state, now, parse_date, packet_agenda_text):
    from datetime import date, datetime, timedelta
    data = json.loads(OUT.read_text()) if OUT.exists() else {"meetings": []}
    have = {m["id"]: m for m in data["meetings"] if m["id"].startswith("adid-")}   # calendar entries are rebuilt each run
    since = (date.today() - timedelta(days=DAYS_BACK)).isoformat()
    docs = []
    for amid, name in council_archives(state).items():
        try:
            for title, url in archive_items(get(f"{BASE}Archive.aspx?AMID={amid}").text):
                d = parse_date(title)
                if d and d >= since and not re.search(r"minutes", title, re.I):
                    docs.append({"id": "adid-" + re.search(r"ADID=(\d+)", url)[1], "title": title, "archive": name, "url": url, "date": d})
        except Exception as err:
            print(f"Council: archive {amid} failed ({err})")
    todo = sorted((d for d in docs if d["id"] not in have), key=lambda d: d["date"], reverse=True)
    print(f"Council: {len(docs)} agendas since {since}, {len(todo)} new to read")
    for d in todo[:MAX_NEW_PER_RUN]:
        try:
            items = items_from_text(packet_agenda_text(d["url"]))
        except Exception as err:
            print(f"  {d['title']}: {err}")
            continue
        for it in items:
            it["kind"] = classify(f"{it['number']} {it['title']}")
        kind = re.sub(r"\s*(agendas?|archive)\s*", " ", d["archive"], flags=re.I).strip() or "City Council"
        have[d["id"]] = {"id": d["id"], "name": kind if COUNCIL.search(kind) else "City Council", "date": d["date"], "time": "",
                         "url": d["url"], "title": d["title"], "items": items, "counts": summarize(items), "has_agenda": True}
        print(f"  {d['date']} {d['title']}: {len(items)} items")
    # upcoming meetings from the calendar that don't have a packet yet
    try:
        dated = {m["date"] for m in have.values()}
        for m in meetings_from_feed():
            try:
                day = datetime.strptime(m["date_text"], "%B %d, %Y").date().isoformat()
            except ValueError:
                continue
            if day >= date.today().isoformat() and day not in dated:
                have["cal-" + m["id"]] = {"id": "cal-" + m["id"], "name": m["name"], "date": day, "time": m["time_text"],
                                          "url": m["url"], "items": [], "counts": {}, "has_agenda": False}
    except Exception as err:
        print(f"Council: calendar feed failed ({err})")
    data["meetings"] = sorted(have.values(), key=lambda m: m["date"], reverse=True)
    data["updated"] = now
    OUT.write_text(json.dumps(data, indent=1))
    print(f"Council: {len(data['meetings'])} meetings on file")
