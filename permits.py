"""
Building permits: map locations, and keeping people's names out.

The city's permit API blocks cloud servers like GitHub's, so permits are
collected in your own browser with collect-permits.js (or the Chrome extension
in tools/permits-extension), which uploads docs/permits.json. This daily step
adds map coordinates to any permit that has an address but no location yet,
and removes people's names from the contractor lists.
"""

import json
import re
from pathlib import Path

PERMITS_FILE = Path("docs/permits.json")

# People's names never go on the site. A contractor "name" that's just a person's ("Matt Hildebrand", "robert") is
# dropped, and a person's name tacked onto a company's ("All Star Services Chad Garrett") is trimmed off.
# collect-permits.js and the site's page apply the same rule.
FIRST_NAMES = set("""
aaron abby abigail adam adrian al alan albert alex alexander alfred alice alicia alison allen allison alyssa
amanda amber amy andre andrea andrew andy angela angie anita ann anna anne annette anthony antonio arnold art
arthur ashley audrey austin barb barbara barry becky ben benjamin bernard beth betty beverly bill billy blake
bob bobby bonnie brad bradley brandon brenda brendan brent brett brian brittany brooke bruce bryan bryce caleb
calvin cameron candace carl carla carlos carmen carol caroline carolyn carrie casey cassandra catherine cathy
chad charlene charles charlie charlotte chelsea cheryl chris christian christina christine christopher chuck
cindy claire clarence clark claudia clifford clint cody colin colleen connie connor corey courtney craig curt
curtis cynthia dale damian damon dan dana daniel danielle danny darin darlene darrell darren darryl daryl dave
david dean deb debbie deborah debra denise dennis derek derrick devin diana diane dick don donald donna donnie
doris dorothy doug douglas drew duane dustin dwayne dwight dylan earl ed eddie edgar edith edward eileen
elaine eleanor eli elijah elizabeth ellen elliot emily emma eric erica erik erin ernest esther ethan eugene
eva evan evelyn florence floyd frances francis frank franklin fred freddie frederick gabriel gail garrett gary
gavin gene geoff geoffrey george gerald gina gloria greg gregg gregory gwen hannah harold harry heather heidi
helen henry herbert holly howard ian irene isaac jack jackie jacob jacqueline jake jamal james jamie jan jane
janet janice jared jason jay jean jeanne jeff jeffery jeffrey jen jenna jennifer jenny jeremiah jeremy jerome
jerry jesse jessica jill jim jimmie jimmy jo joan joann joanne joe joel joey john johnny jon jonathan jorge
jose joseph josephine josh joshua joyce juan judith judy julia julian julie justin karen karl kate katherine
kathleen kathryn kathy katie kay kayla keith kelly ken kendra kenneth kenny kevin kim kimberly kirk kristen
kristin kristina kristy kurt kyle lance larry laura lauren lawrence leah lee leo leon leonard leroy leslie
linda lindsay lisa lloyd lois lonnie lori lorraine louis louise lucas lucy luis luke malcolm mandy manuel marc
marcia marcus margaret maria marie marilyn mario marion marjorie mark martha martin marvin mary matt matthew
maureen max megan melanie melinda melissa meredith michael michele michelle mickey miguel mike mildred mitch
mitchell molly monica nancy natalie nathan nathaniel neal neil nicholas nick nicole nina noah norma norman
olivia omar oscar owen paige pam pamela pat patricia patrick paul paula pauline pedro peggy penny pete peter
phil philip phillip phyllis rachel ralph randall randy ray raymond rebecca reggie regina renee rhonda richard
rick ricky rita rob robbie robert roberta roberto robin rod rodney roger roland ron ronald ronnie rosemary
ross roy ruben russ russell ruth ryan sabrina sally sam samantha samuel sandra sandy sara sarah scott sean
seth shane shannon sharon shaun shawn sheila shelby shelly sherry shirley sidney sophia stacey stacy stan
stanley stephanie stephen steve steven stuart sue susan suzanne sylvia tamara tammy tanya tara ted teresa
terrence terry theodore theresa thomas tiffany tim timothy tina toby todd tom tommy tony tonya tracey tracy
travis trent trevor troy tyler valerie vanessa vernon veronica vicki vickie victor victoria vince vincent
virginia vivian walter wanda warren wayne wendy wesley whitney william willie yolanda zach zachary zack
""".split())
BIZ = re.compile(r"\b(?:" + (
    r"LLC|L\.L\.C\.?|Inc\.?|Incorporated|Corp\.?|Corporation|Company|Co|Ltd\.?|LP|Plumbing|Electric(?:al)?|"
    r"Roofing|Heating|Cooling|HVAC|Mechanical|Construction|Builders?|Homes|Remodel(?:ing|ers)?|"
    r"Renovations?|Services?|Contracting|Contractors?|Exteriors|Fenc(?:e|ing)|Pools?|Solar|Energy|"
    r"Restoration|Waterproofing|Concrete|Masonry|Excavating|Landscap(?:e|ing)|Windows|Siding|Gutters|Sewer|"
    r"Drain(?:age)?|Insulation|Paving|Design(?:s)?|Development|Enterprises|Group|Systems|Air|Comfort|Glass|"
    r"Doors|Basements?|Foundations?|Demolition|Signs?|Plumbers|Roof|Builds?|Realty|Properties|Investors|"
    r"Partners|Associates|Solutions|Pros|Team|Brothers|Bros|Sons|Works|Depot|Supply"
) + r")\b", re.I)


def looks_like_person(name):
    words = str(name).split()
    if not words or BIZ.search(name) or re.sub(r"[^a-z]", "", words[0].lower()) not in FIRST_NAMES:
        return False
    return len(words) <= 2 or (len(words) == 3 and re.fullmatch(r"[A-Za-z]\.?", words[1]) is not None)


def clean_name(name):
    name = " ".join(str(name).split())
    cut = re.sub(r"(?:\s+[-–]\s*|\s*[-–]\s+)[A-Za-z][\w'’]*$", "", name)  # "Columbus Worthington Air- Daubenmire"
    if cut != name and BIZ.search(cut):
        name = cut
    m = re.match(r"^(.*\S)\s+([A-Za-z]+)\s+[A-Za-z'’-]+$", name)  # "All Star Services Chad Garrett"
    if m and m.group(2).lower() in FIRST_NAMES and BIZ.search(m.group(1)):
        name = m.group(1)
    return name


def company_names(names):
    out = []
    for n in names or []:
        n = clean_name(n)
        if n and not looks_like_person(n) and n not in out:
            out.append(n)
    return out


def run(state, geocode, now):
    state.pop("permits", None)  # left over from the old server-side collector
    if not PERMITS_FILE.exists():
        print("Permits: no docs/permits.json yet")
        return []
    data = json.loads(PERMITS_FILE.read_text())
    # older collector versions saved a sample of a record page's text, which holds people's names and contact
    # details; it never belongs in this public file
    scrubbed = data.pop("record_labels", None) is not None
    permits = data.get("permits", [])
    for p in permits:
        if p.get("contractors"):
            kept = company_names(p["contractors"])
            if kept != p["contractors"]:
                p["contractors"] = kept
                scrubbed = True
    added = 0
    for p in permits:
        if p.get("address") and p.get("lat") is None:
            coords = geocode(p["address"])
            if coords:
                p["lat"], p["lon"] = coords
                added += 1
    if added or scrubbed:
        PERMITS_FILE.write_text(json.dumps(data, indent=2, sort_keys=True))
    missing = sum(1 for p in permits if not p.get("address"))
    print(f"Permits: {len(permits)} total, {added} newly mapped, {missing} without an address yet")
    return []
