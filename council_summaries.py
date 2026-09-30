"""
Plain-language AI summaries of City Council agendas.

For each council meeting from SUMMARIES_FROM on that has an agenda, sends the agenda's text to
Claude and asks for a short summary written for residents: what's on the agenda, what each item
would do, why it matters, and where it stands (first reading, vote expected, and so on), plus a few
terms explained. The summary is saved on the meeting in docs/council.json under "ai" and is made
again only if the agenda changes. Needs the ANTHROPIC_API_KEY secret; without it, this step is skipped.
Runs from council.py.
"""

import io
import json
import os
import re
from datetime import datetime, timezone

import requests

SUMMARIES_FROM = "2026-09-28"
MODEL = "claude-opus-5-5"
MAX_PER_RUN = 10
MAX_PAGES, MAX_CHARS = 40, 120_000
UA = "grandview-zoning-watch/1.1 (community site, runs once daily)"

SYSTEM = """You explain Grandview Heights, Ohio City Council agendas to residents who don't follow city \
government closely. You get the text of one meeting's agenda (sometimes with the meeting packet's \
supporting pages after it). Write in plain, friendly language at about an eighth-grade reading level.

- Say what each item would actually do and why a resident might care (money, streets, housing, \
businesses, taxes, services). Name specific places, amounts, and dates when the agenda gives them.
- Group routine items (proclamations, recognitions, minutes) briefly; spend words on items with real effects.
- An agenda lists what council will consider, not what it decided. Never say something passed or \
failed. Use "would", "proposes", "is up for".
- Explain the stage when the agenda shows it: a first reading is an introduction (no vote yet); \
second and third readings come before a vote; an emergency clause means it can pass at this meeting.
- Only use what the agenda says. If an item's purpose isn't clear from the text, say so briefly \
instead of guessing.
- Leave out people's names except elected officials and business names that are part of an item."""

SCHEMA = {
    "type": "object",
    "properties": {
        "overview": {"type": "string", "description": "2 to 4 sentences: what this meeting is mostly about, in plain words."},
        "items": {
            "type": "array",
            "description": "The meaningful agenda items, most important first.",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "A short plain-language name for the item, with its number if it has one (e.g. Ord. 2026-16)."},
                    "what": {"type": "string", "description": "What it would do, in one or two sentences."},
                    "why": {"type": "string", "description": "Why a resident might care, in one sentence. Empty if routine."},
                    "stage": {"type": "string", "description": "Where it stands, e.g. 'First reading, no vote yet', 'Vote expected', 'Routine'. Empty if unclear."},
                },
                "required": ["title", "what", "why", "stage"],
                "additionalProperties": False,
            },
        },
        "terms": {
            "type": "array",
            "description": "Up to 4 government terms from this agenda a resident might not know, explained simply.",
            "items": {
                "type": "object",
                "properties": {"term": {"type": "string"}, "meaning": {"type": "string"}},
                "required": ["term", "meaning"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["overview", "items", "terms"],
    "additionalProperties": False,
}


def agenda_text(url):
    r = requests.get(url, headers={"User-Agent": UA}, timeout=90)
    r.raise_for_status()
    if not r.content.startswith(b"%PDF"):
        raise ValueError("agenda isn't a PDF")
    from pypdf import PdfReader
    text = "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(r.content)).pages[:MAX_PAGES])
    return re.sub(r"[ \t]+", " ", text)[:MAX_CHARS]


def summarize(client, meeting, text):
    """A summary dict from Claude, or None if it declined."""
    import anthropic
    response = client.beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        system=SYSTEM,
        messages=[{"role": "user", "content":
                   f"Meeting: {meeting['name']} on {meeting['date']}\n\n<agenda>\n{text}\n</agenda>"}],
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",   # if a request is declined, the API retries it on another model
    )
    if response.stop_reason == "refusal":
        print(f"  {meeting['date']}: declined ({getattr(response.stop_details, 'category', None)})")
        return None
    if response.stop_reason == "max_tokens":
        print(f"  {meeting['date']}: summary cut off")
        return None
    out = json.loads(next(b.text for b in response.content if b.type == "text"))
    out["model"] = response.model
    return out


def run(meetings):
    """Adds or refreshes "ai" on eligible meetings in place."""
    todo = [m for m in meetings if m.get("date", "") >= SUMMARIES_FROM and m.get("has_agenda")
            and (m.get("ai") or {}).get("source") != m["url"]]
    if not todo:
        return
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(f"Council summaries: {len(todo)} meetings need one, but ANTHROPIC_API_KEY isn't set; skipping")
        return
    import anthropic
    client = anthropic.Anthropic()
    done = 0
    for m in sorted(todo, key=lambda m: m["date"])[:MAX_PER_RUN]:
        try:
            s = summarize(client, m, agenda_text(m["url"]))
        except (anthropic.APIError, requests.RequestException, ValueError) as err:
            print(f"  {m['date']} {m['name']}: summary failed ({err})")
            continue
        if s:
            s.update(source=m["url"], generated=datetime.now(timezone.utc).isoformat(timespec="seconds"))
            m["ai"] = s
            done += 1
    print(f"Council summaries: {done} written, {len(todo) - done} still to do")
