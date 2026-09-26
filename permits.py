"""
Building permits: geocoding only.

The city's permit API blocks cloud servers like GitHub's, so permits are
collected in your own browser with collect-permits.js, which downloads a
permits.json you upload to docs/. This daily step just adds map
coordinates to any permit that has an address but no location yet.
"""

import json
from pathlib import Path

PERMITS_FILE = Path("docs/permits.json")


def run(state, geocode, now):
    state.pop("permits", None)  # left over from the old server-side collector
    if not PERMITS_FILE.exists():
        print("Permits: no docs/permits.json yet")
        return []
    data = json.loads(PERMITS_FILE.read_text())
    permits = data.get("permits", [])
    added = 0
    for p in permits:
        if p.get("address") and p.get("lat") is None:
            coords = geocode(p["address"])
            if coords:
                p["lat"], p["lon"] = coords
                added += 1
    if added:
        PERMITS_FILE.write_text(json.dumps(data, indent=2, sort_keys=True))
    missing = sum(1 for p in permits if not p.get("address"))
    print(f"Permits: {len(permits)} total, {added} newly mapped, {missing} without an address yet")
    return []
