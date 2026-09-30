"""
Temporary: lists the folders on the city's Public Documents page, in case council agendas live in
the Document Center rather than on the calendar. Writes docs/probe.json. Removed once agendas are found.
"""

import json
import re
from html import unescape
from pathlib import Path
from urllib.parse import urljoin

import requests

OUT = Path("docs/probe.json")
BASE = "https://www.grandviewheights.gov/"
UA = "grandview-zoning-watch/1.1 (community site, runs once daily)"


def links(url):
    html = requests.get(url, headers={"User-Agent": UA}, timeout=60).text
    return [(re.sub(r"<[^>]+>|\s+", " ", unescape(t)).strip(), urljoin(url, unescape(h)))
            for h, t in re.findall(r'<a\b[^>]*href="([^"#]+)"[^>]*>(.*?)</a>', html, re.I | re.S)]


def run(state, now):
    report = {"updated": now}
    try:
        home = links(BASE)
        pub = [u for t, u in home if re.search(r"public documents", t, re.I)]
        report["public_documents_pages"] = pub[:5]
        for u in pub[:2]:
            report[u] = [(t, l) for t, l in links(u) if re.search(r"DocumentCenter|council|agenda|minutes", t + l, re.I)][:80]
        report["agenda_links_on_home"] = [(t, u) for t, u in home if re.search(r"agenda|council|minutes", t + u, re.I)][:40]
    except Exception as err:
        report["error"] = str(err)[:300]
    OUT.write_text(json.dumps(report, indent=1))
    print("Probe: public documents", report.get("public_documents_pages"))
