"""
City Council agendas, summarized.

The city posts council meetings on its website calendar (Calendar.aspx), and each meeting's page
links its agenda. Each run reads the calendar's RSS feed for council meetings (council and its
committees), opens each meeting's page, follows the agenda link, and pulls out the agenda's items:
ordinances, resolutions, public hearings, and so on, in the agenda's own words (no model writes the
summary). Meetings are kept once found, so the history builds up. Writes docs/council.json.
Runs from scrape.py.
"""

import io
import json
import re
from html import unescape
from pathlib import Path
from urllib.parse import urljoin

import requests

BASE = "https://www.grandviewheights.gov/"
FEEDS = [BASE + "RSSFeed.aspx?ModID=58&CID=All-calendar.xml"]
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


def agenda_links(event_url):
    """Links on a meeting's calendar page that look like its agenda."""
    html = get(event_url).text
    links = []
    for href, text in re.findall(r'<a\b[^>]*href="([^"#]+)"[^>]*>(.*?)</a>', html, re.I | re.S):
        text = re.sub(r"<[^>]+>|\s+", " ", unescape(text)).strip()
        url = urljoin(BASE, unescape(href))
        if re.search(r"DocumentCenter/View|AgendaCenter/ViewFile|\.pdf|ViewFile|Archive\.aspx\?ADID", url, re.I) and \
                not re.search(r"minutes", text + url, re.I):
            links.append((text, url))
    links.sort(key=lambda l: 0 if re.search(r"agenda", l[0] + l[1], re.I) else 1)
    return links


def pdf_text(url):
    r = get(url)
    if not r.content.startswith(b"%PDF"):
        raise ValueError("not a PDF")
    from pypdf import PdfReader
    return "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(r.content)).pages[:15])


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


def run(state, now):
    from datetime import datetime
    data = json.loads(OUT.read_text()) if OUT.exists() else {"meetings": []}
    have = {m["id"]: m for m in data["meetings"]}
    try:
        found = meetings_from_feed()
    except Exception as err:
        print(f"Council: couldn't read the calendar feed ({err})")
        return
    todo = [m for m in found if not have.get(m["id"], {}).get("items")]
    print(f"Council: {len(found)} council meetings in the calendar feed, {len(todo)} to read")
    for m in todo[:MAX_NEW_PER_RUN]:
        try:
            d = datetime.strptime(m["date_text"], "%B %d, %Y").date().isoformat() if m["date_text"] else ""
        except ValueError:
            d = ""
        items, agenda = [], None
        try:
            links = agenda_links(m["url"])
            print(f"  {m['name']} {d}: links {[t for t, u in links][:6]}")
            for text, url in links[:3]:
                try:
                    items = items_from_text(pdf_text(url))
                    agenda = url
                    break
                except Exception as err:
                    print(f"    {url}: {err}")
        except Exception as err:
            print(f"  {m['url']}: {err}")
        for it in items:
            it["kind"] = classify(f"{it['number']} {it['title']}")
        have[m["id"]] = {"id": m["id"], "name": m["name"], "date": d, "time": m["time_text"], "url": agenda or m["url"],
                         "event_url": m["url"], "items": items, "counts": summarize(items), "has_agenda": bool(agenda)}
    data["meetings"] = sorted(have.values(), key=lambda m: m["date"], reverse=True)
    data["updated"] = now
    OUT.write_text(json.dumps(data, indent=1))
    print(f"Council: {len(data['meetings'])} meetings on file")
