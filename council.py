"""
City Council agendas, summarized.

The city posts each council meeting's packet (agenda first, then supporting documents) in the
Document Center's "City Council Meeting Packets" folder, linked from its Public Documents page.
Each run opens that folder (it loads with JavaScript, so in a headless browser), reads the agenda
pages at the front of each new packet, and pulls out the items: ordinances, resolutions, public
hearings, and so on, in the agenda's own words (no model writes the summary). Upcoming council
meetings from the city calendar's RSS feed are listed too, until their packet is posted.
Meetings are kept once read, so the history builds up. Writes docs/council.json. Runs from scrape.py.
"""

import json
import re
from html import unescape
from pathlib import Path

import requests

BASE = "https://www.grandviewheights.gov/"
FEEDS = [BASE + "RSSFeed.aspx?ModID=58&CID=All-calendar.xml"]
PACKETS_FOLDER = 18          # Document Center: City Council Meeting Packets
MAX_FOLDERS = 30
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


def packets(parse_date):
    """[{id, name, url, date}] for documents in the packets folder and its year/month subfolders."""
    from playwright.sync_api import sync_playwright
    docs, seen, queue = {}, set(), [(str(PACKETS_FOLDER), "", 0)]
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(user_agent=UA)
        while queue and len(seen) < MAX_FOLDERS:
            fid, fname, depth = queue.pop(0)
            if fid in seen:
                continue
            seen.add(fid)
            page.goto(f"{BASE}DocumentCenter/Index/{fid}", wait_until="networkidle", timeout=60000)
            page.wait_for_timeout(1500)
            found = page.eval_on_selector_all("a[href*='DocumentCenter/']",
                                              "els => els.map(e => ({href: e.href, text: (e.textContent || '').trim()}))")
            print(f"  packets folder {fid} '{fname}': {[(l['text'][:50], l['href'][-40:]) for l in found][:40]}")
            for l in found:
                if m := re.search(r"DocumentCenter/View/(\d+)", l["href"]):
                    name = l["text"] or l["href"].rsplit("/", 1)[-1].replace("-", " ")
                    docs.setdefault(m[1], {"id": "doc-" + m[1], "name": name, "url": l["href"].split("?")[0],
                                           "date": parse_date(name) or parse_date(l["href"]) or parse_date(fname), "folder": fname})
                elif (m := re.search(r"DocumentCenter/Index/(\d+)", l["href"])) and depth < 2 and m[1] not in seen \
                        and re.search(r"20\d\d|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|council|packet|agenda", l["text"], re.I):
                    queue.append((m[1], l["text"], depth + 1))
        b.close()
    print(f"Council: {len(docs)} documents in {len(seen)} packet folders")
    return list(docs.values())


def run(state, now, parse_date, packet_agenda_text):
    from datetime import date, datetime
    data = json.loads(OUT.read_text()) if OUT.exists() else {"meetings": []}
    have = {m["id"]: m for m in data["meetings"] if m["id"].startswith("doc-")}   # calendar entries are rebuilt each run
    try:
        docs = [d for d in packets(parse_date) if not re.search(r"minutes", d["name"], re.I)]
    except Exception as err:
        print(f"Council: couldn't read the packets folder ({err})")
        docs = []
    todo = sorted((d for d in docs if d["id"] not in have and d["date"]), key=lambda d: d["date"], reverse=True)
    print(f"Council: {len(docs)} packets, {len(todo)} new to read")
    for d in todo[:MAX_NEW_PER_RUN]:
        try:
            items = items_from_text(packet_agenda_text(d["url"]))
        except Exception as err:
            print(f"  {d['name']}: {err}")
            continue
        for it in items:
            it["kind"] = classify(f"{it['number']} {it['title']}")
        name = re.sub(r"\s*(packet|agenda)\s*", " ", re.sub(r"\b\d{1,2}[-_.]\d{1,2}[-_.]\d{2,4}\b|\b20\d\d\b", "", d["name"]), flags=re.I).strip(" -_") or "City Council"
        have[d["id"]] = {"id": d["id"], "name": name if COUNCIL.search(name) else "City Council " + name, "date": d["date"], "time": "",
                         "url": d["url"], "items": items, "counts": summarize(items), "has_agenda": True}
        print(f"  {d['date']} {d['name']}: {len(items)} items")
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
