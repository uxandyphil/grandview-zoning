// Grandview permit collector
// 1. Open https://grandviewheightsoh.portal.opengov.com/search
// 2. Open the browser console (right-click > Inspect > Console)
// 3. Paste this whole file and press Enter
// A panel appears in the corner. When it says "Done", click Download and upload
// the file to docs/permits.json in the grandview-zoning repo.
// (The Chrome extension in tools/permits-extension runs this same script with window.GVZ_AUTO set: no clicks,
// and the result is left in window.GVZ_RESULT for the extension to publish.)
(async () => {
  const API = window.GVZ_API || "https://api-east.viewpointcloud.com/v2/grandviewheightsoh";
  const SITE_JSON = "https://uxandyphil.github.io/grandview-zoning/permits.json";
  const OLD_PREFIXES = ["R", "E", "H", "P", "ZON", "B", "C", "D", "F", "M", "S", "Z", "ROW", "SIGN", "DEMO"];
  // ROOF, WIND (windows), SIDE (siding), GAS, TAP and SWR series show up in the county's records from 2024 on
  const PREFIXES = OLD_PREFIXES.concat(["ROOF", "WIND", "SIDE", "GAS", "TAP", "SWR", "FENC", "POOL", "SOL"]);
  // Code enforcement (violations), if the city makes those records public. Common series names are tried,
  // and a few keyword searches reveal any other series whose record type looks like enforcement.
  const ENFORCE_PREFIXES = ["CE", "CODE", "ENF", "VIOL", "COMP", "PM", "NOV", "SWO", "NUIS"];
  const ENFORCE_TYPE = /violat|enforce|complaint|nuisance|property maint|stop work|citation/i;
  const ENFORCE_WORDS = ["violation", "code enforcement", "complaint", "property maintenance", "nuisance"];
  PREFIXES.push(...ENFORCE_PREFIXES);
  const LOOKAHEAD = 12;      // misses in a row before a series stops
  const MAX_DETAILS = window.GVZ_MAX_DETAILS || 200;   // record pages per click; run again to continue
  const FRAME_RECYCLE = 10;  // when whole record pages must be read: one hidden frame, rebuilt this often
  const FRAME_LIMIT = 80;    // and at most this many pages per run
  const YEARS_BACK = 3;      // also collect this many past years (finished years are only walked once)
  const PAUSE = 1500;        // ms between searches
  const PAGE_PAUSE = 3000;   // ms between permit pages (loading one is many requests)
  const RECHECK = 12 * 3600_000;   // look for new permits (and retry the data lists) at most this often:
                                   // a run's later batches only read pages
  const REFUSED = /\bHTTP (401|403|429)\b/;   // the portal saying no, or "slow down": stop asking instead of pushing on
  const SEARCH_REST = 3600_000;   // after the portal refuses searches, don't search again for this long
  const STORE = "gvz-permits-v1";
  // permit types ("HVAC Permit", "Re-Roofing Permit", "Sewer Lateral Replacement/Repair") are never contractors
  const looksLikeType = (n) => /\bpermits?$|\bcertificate\b|\blateral\b|replacement\/repair|registration$|application$/i.test(String(n).trim());
  // Business words: a name with one of these is a company's
  const TRADE = "LLC|L\\.L\\.C\\.?|Inc\\.?|Incorporated|Corp\\.?|Corporation|Company|Co|Ltd\\.?|LP|Plumbing|Electric(?:al)?|" +
    "Roofing|Heating|Cooling|HVAC|Mechanical|Construction|Builders?|Homes|Remodel(?:ing|ers)?|Renovations?|" +
    "Services?|Contracting|Contractors?|Exteriors|Fenc(?:e|ing)|Pools?|Solar|Energy|Restoration|Waterproofing|" +
    "Concrete|Masonry|Excavating|Landscap(?:e|ing)|Windows|Siding|Gutters|Sewer|Drain(?:age)?|Insulation|" +
    "Paving|Design(?:s)?|Development|Enterprises|Group|Systems|Air|Comfort|Glass|Doors|Basements?|Foundations?|" +
    "Demolition|Signs?|Plumbers|Roof|Builds?|Realty|Properties|Investors|Partners|Associates|Solutions|Pros|" +
    "Team|Brothers|Bros|Sons|Works|Depot|Supply";
  const bizRe = new RegExp(`\\b(?:${TRADE})\\b`, "i");
  const tradeRe = new RegExp(`\\b(?:${TRADE.replace(/^.*?\|LP\|/, "")})\\b`, "i");   // trade words only, no LLC/Inc
  // People's names never go on the site. A contractor "name" that's just a person's ("Matt Hildebrand", "robert") is
  // left out, and a person's name tacked onto a company's ("All Star Services Chad Garrett") is trimmed off.
  // (permits.py and the site's page apply the same rule.)
  const FIRST_NAMES = new Set(("aaron abby abigail adam adrian al alan albert alex alexander alfred alice alicia alison allen allison alyssa " +
    "amanda amber amy andre andrea andrew andy angela angie anita ann anna anne annette anthony antonio arnold art " +
    "arthur ashley audrey austin barb barbara barry becky ben benjamin bernard beth betty beverly bill billy blake " +
    "bob bobby bonnie brad bradley brandon brenda brendan brent brett brian brittany brooke bruce bryan bryce caleb " +
    "calvin cameron candace carl carla carlos carmen carol caroline carolyn carrie casey cassandra catherine cathy " +
    "chad charlene charles charlie charlotte chelsea cheryl chris christian christina christine christopher chuck " +
    "cindy claire clarence clark claudia clifford clint cody colin colleen connie connor corey courtney craig curt " +
    "curtis cynthia dale damian damon dan dana daniel danielle danny darin darlene darrell darren darryl daryl dave " +
    "david dean deb debbie deborah debra denise dennis derek derrick devin diana diane dick don donald donna donnie " +
    "doris dorothy doug douglas drew duane dustin dwayne dwight dylan earl ed eddie edgar edith edward eileen elaine " +
    "eleanor eli elijah elizabeth ellen elliot emily emma eric erica erik erin ernest esther ethan eugene eva evan " +
    "evelyn florence floyd frances francis frank franklin fred freddie frederick gabriel gail garrett gary gavin gene " +
    "geoff geoffrey george gerald gina gloria greg gregg gregory gwen hannah harold harry heather heidi helen henry " +
    "herbert holly howard ian irene isaac jack jackie jacob jacqueline jake jamal james jamie jan jane janet janice " +
    "jared jason jay jean jeanne jeff jeffery jeffrey jen jenna jennifer jenny jeremiah jeremy jerome jerry jesse " +
    "jessica jill jim jimmie jimmy jo joan joann joanne joe joel joey john johnny jon jonathan jorge jose joseph " +
    "josephine josh joshua joyce juan judith judy julia julian julie justin karen karl kate katherine kathleen " +
    "kathryn kathy katie kay kayla keith kelly ken kendra kenneth kenny kevin kim kimberly kirk kristen kristin " +
    "kristina kristy kurt kyle lance larry laura lauren lawrence leah lee leo leon leonard leroy leslie linda lindsay " +
    "lisa lloyd lois lonnie lori lorraine louis louise lucas lucy luis luke malcolm mandy manuel marc marcia marcus " +
    "margaret maria marie marilyn mario marion marjorie mark martha martin marvin mary matt matthew maureen max megan " +
    "melanie melinda melissa meredith michael michele michelle mickey miguel mike mildred mitch mitchell molly monica " +
    "nancy natalie nathan nathaniel neal neil nicholas nick nicole nina noah norma norman olivia omar oscar owen " +
    "paige pam pamela pat patricia patrick paul paula pauline pedro peggy penny pete peter phil philip phillip " +
    "phyllis rachel ralph randall randy ray raymond rebecca reggie regina renee rhonda richard rick ricky rita rob " +
    "robbie robert roberta roberto robin rod rodney roger roland ron ronald ronnie rosemary ross roy ruben russ " +
    "russell ruth ryan sabrina sally sam samantha samuel sandra sandy sara sarah scott sean seth shane shannon sharon " +
    "shaun shawn sheila shelby shelly sherry shirley sidney sophia stacey stacy stan stanley stephanie stephen steve " +
    "steven stuart sue susan suzanne sylvia tamara tammy tanya tara ted teresa terrence terry theodore theresa thomas " +
    "tiffany tim timothy tina toby todd tom tommy tony tonya tracey tracy travis trent trevor troy tyler valerie " +
    "vanessa vernon veronica vicki vickie victor victoria vince vincent virginia vivian walter wanda warren wayne " +
    "wendy wesley whitney william willie yolanda zach zachary zack").split(" "));
  const looksLikePerson = (n) => {
    const w = String(n).trim().split(/\s+/);
    if (bizRe.test(n) || !FIRST_NAMES.has(w[0].toLowerCase().replace(/[^a-z]/g, ""))) return false;
    return w.length <= 2 || (w.length === 3 && /^[a-z]\.?$/i.test(w[1]));
  };
  const cleanName = (n) => {
    n = String(n).replace(/\s+/g, " ").trim();
    const cut = n.replace(/(?:\s+[-–]\s*|\s*[-–]\s+)[A-Za-z][\w'’]*$/, "");   // "Columbus Worthington Air- Daubenmire"
    if (cut !== n && bizRe.test(cut)) n = cut;
    const m = /^(.*\S)\s+([A-Za-z]+)\s+[A-Za-z'’-]+$/.exec(n);              // "All Star Services Chad Garrett"
    if (m && FIRST_NAMES.has(m[2].toLowerCase()) && bizRe.test(m[1])) n = m[1];
    return n;
  };

  if (!window.GVZ_TEST && !location.hostname.endsWith("portal.opengov.com")) {
    alert("Open grandviewheightsoh.portal.opengov.com first, then paste this in its console.");
    return;
  }
  document.getElementById("gvz-panel")?.remove();

  // ---------- panel ----------
  const panel = document.createElement("div");
  panel.id = "gvz-panel";
  panel.style.cssText = "position:fixed;right:16px;bottom:16px;z-index:2147483647;width:340px;" +
    "background:#fff;color:#15201B;border:1px solid #ccd3cf;border-radius:12px;padding:14px;" +
    "font:14px/1.45 system-ui,sans-serif;box-shadow:0 8px 30px rgba(0,0,0,.18)";
  panel.innerHTML = `<div style="font-weight:700;margin-bottom:6px">Grandview permit collector</div>
    <div id="gvz-status" style="margin-bottom:8px">Starting…</div>
    <div id="gvz-log" style="max-height:160px;overflow:auto;font-size:12px;color:#5E6B64;margin-bottom:10px"></div>
    <div style="display:flex;gap:8px">
      <button id="gvz-dl" disabled style="flex:1;padding:8px;border-radius:8px;border:0;background:#15201B;color:#fff;font:inherit;cursor:pointer">Download permits.json</button>
      <button id="gvz-stop" style="padding:8px 12px;border-radius:8px;border:1px solid #ccd3cf;background:#fff;font:inherit;cursor:pointer">Stop</button>
    </div>`;
  document.body.appendChild(panel);
  const $ = (id) => panel.querySelector("#" + id);
  const status = (t) => { $("gvz-status").textContent = t; window.GVZ_STATUS = t; };
  window.GVZ_RESULT = null;
  const log = (t) => { const d = document.createElement("div"); d.textContent = t; $("gvz-log").prepend(d); console.log("[permits]", t); };
  let stopped = false;
  $("gvz-stop").onclick = () => { stopped = true; status("Stopping after this request…"); };
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  // ---------- saved data ----------
  let permits = {};
  try { permits = JSON.parse(localStorage.getItem(STORE) || "{}"); } catch (e) { permits = {}; }
  if (!Object.keys(permits).length) {
    try {
      const r = await fetch(SITE_JSON, { cache: "no-store" });
      if (r.ok) for (const p of (await r.json()).permits || []) permits[p.number] = p;
      log(`Loaded ${Object.keys(permits).length} permits from your site`);
    } catch (e) { log("Starting fresh (couldn't load the site's copy)"); }
  } else {
    log(`Loaded ${Object.keys(permits).length} permits saved in this browser`);
    // contractor names already on the site fill in any this browser lost (an earlier version could erase them)
    try {
      const r = await fetch(SITE_JSON, { cache: "no-store" });
      let filled = 0;
      if (r.ok) for (const sp of (await r.json()).permits || []) {
        const lp = permits[sp.number];
        const names = (sp.contractors || []).map(cleanName).filter((n) => !looksLikeType(n) && !looksLikePerson(n));
        if (lp && !(lp.contractors || []).length && names.length) { lp.contractors = names; filled++; }
        // map locations come from the site's nightly job; keep them so an upload doesn't take the pins off the map
        if (lp && lp.lat == null && sp.lat != null) { lp.lat = sp.lat; lp.lon = sp.lon; }
      }
      if (filled) log(`Restored contractor names on ${filled} permits from your site`);
    } catch (e) {}
  }
  // people's names never go to the site, including any an older version saved in this browser
  for (const p of Object.values(permits))
    if ((p.contractors || []).length) p.contractors = [...new Set(p.contractors.map(cleanName))].filter((n) => !looksLikePerson(n));
  const save = () => localStorage.setItem(STORE, JSON.stringify(permits));
  // field names and on-page labels from one record (no values), so the site can learn where contractors are listed
  let recordFields = null, recordForm = null, recordForms = {};
  const CONTRACTOR_VERSION = 7;   // permits read by an older version are read again (6 could count a page that never loaded)
  let recordShapes = null;        // field names (never values) of each data list, to check the form matching
  let recordStatus = null;        // what each data list answered on the first permit (200 = OK)

  function payload() {
    const list = Object.values(permits).sort((a, b) =>
      ((b.date || b.first_seen.slice(0, 10)) + String(b.entity_id).padStart(9, "0"))
        .localeCompare((a.date || a.first_seen.slice(0, 10)) + String(a.entity_id).padStart(9, "0")));
    const meta = { updated: new Date().toISOString(), record_fields: recordFields, record_form: recordForm, record_forms: recordForms,
      record_shapes: recordShapes, record_status: recordStatus,
      record_apis: recordApis && recordApis.map((u) => u.replace(/[?&](token|key|auth)[^&]*/gi, "")) };
    return JSON.stringify({ ...meta, permits: list }, null, 2);
  }
  function download() {
    const blob = new Blob([payload()], { type: "application/json" });
    const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(blob), download: "permits.json" });
    document.body.appendChild(a); a.click(); a.remove();
  }
  $("gvz-dl").onclick = download;

  // ---------- API ----------
  let headers = {};
  // header names in lower case, so a fresh value replaces an old one instead of both being sent
  const lowerKeys = (h) => Object.fromEntries(Object.entries(h || {}).map(([k, v]) => [k.toLowerCase(), v]));
  try { headers = lowerKeys(JSON.parse(localStorage.getItem(STORE + "-headers") || "{}")); } catch (e) {}
  const FORBIDDEN = /^(host|origin|referer|user-agent|cookie|connection|content-length|priority|if-none-match|if-modified-since|accept-encoding|sec-)/i;

  function captureHeaders() {
    return new Promise((resolve) => {
      const keep = (h) => { const o = {}; for (const [k, v] of Object.entries(h)) if (!FORBIDDEN.test(k)) o[k.toLowerCase()] = v; resolve(o); };
      const open = XMLHttpRequest.prototype.open, setH = XMLHttpRequest.prototype.setRequestHeader,
        send = XMLHttpRequest.prototype.send, f = window.fetch;
      XMLHttpRequest.prototype.open = function (m, u) { this._gvzU = String(u); this._gvzH = {}; return open.apply(this, arguments); };
      XMLHttpRequest.prototype.setRequestHeader = function (k, v) { if (this._gvzH) this._gvzH[k] = v; return setH.apply(this, arguments); };
      XMLHttpRequest.prototype.send = function () { if (/search_results/.test(this._gvzU || "")) keep(this._gvzH); return send.apply(this, arguments); };
      window.fetch = function (input, init) {
        const u = String((input && input.url) || input);
        if (/search_results/.test(u)) {
          const h = {}; new Headers((init && init.headers) || (input && input.headers) || {}).forEach((v, k) => { h[k] = v; });
          keep(h);
        }
        return f.apply(this, arguments);
      };
    });
  }

  async function search(key) {
    const q = new URLSearchParams({ criteria: "record", key, timeStamp: Date.now(), ignoreCommunity: "true" });
    const r = await fetch(`${API}/search_results?${q}`, { headers });
    if (!r.ok) throw new Error("HTTP " + r.status);
    return r.json();
  }

  let done = {};
  try { done = JSON.parse(localStorage.getItem(STORE + "-done") || "{}"); } catch (e) {}
  const saveDone = () => localStorage.setItem(STORE + "-done", JSON.stringify(done));
  const due = (key) => Date.now() - (done[key] || 0) >= RECHECK;   // not done in the last 12 hours

  // Searching only finds new permits. If the portal refuses it, the run doesn't stop: it skips looking for new
  // permits (and doesn't search again for an hour) and still reads the pages of the permits it already has.
  let searchRefused = Date.now() - (done["search:refusedAt"] || 0) < SEARCH_REST ? "less than an hour ago" : null;
  function searchWasRefused(why) {
    searchRefused = why;
    done["search:refusedAt"] = Date.now(); saveDone();
    log(`The portal refused searches (${why}), so this run skips looking for new permits and reads the ones it has`);
  }
  // Make sure searching works; if not, borrow the headers the portal's own search uses
  let firstData = null;
  if (searchRefused) log("The portal refused searches less than an hour ago, so this run only reads the permits it has");
  else {
    try { firstData = await search("R-26-82"); }
    catch (e) {
      status("One step from you: type R-26-82 in the portal's search box and press Enter.");
      log(`Direct search failed (${e.message}); waiting for you to search once`);
      const borrowed = await Promise.race([captureHeaders(), sleep(300_000).then(() => null)]);   // up to 5 minutes
      if (borrowed) { headers = borrowed; localStorage.setItem(STORE + "-headers", JSON.stringify(headers)); }
      await sleep(1500);
      try { firstData = await search("R-26-82"); log("Got it, searching works now"); }
      catch (e2) { searchWasRefused(e2.message); }
    }
  }

  // ---------- finding record numbers ----------
  const numRe = /\b([A-Z]{1,5})-(\d{2})-0*(\d{1,6})\b/i;
  const norm = (t) => { const m = numRe.exec(String(t || "")); return m ? `${m[1].toUpperCase()}-${m[2]}-${+m[3]}` : null; };
  const pool = {}, searched = new Set();
  let searches = 0;
  const harvest = (data) => {
    for (const it of Array.isArray(data) ? data : ((data && (data.results || data.value)) || [])) {
      // enforcement records keep their own number even if it isn't shaped like a permit number
      const n = norm(it.resultText) || (ENFORCE_TYPE.test(it.secondaryText || "") && String(it.resultText || "").trim()) || null;
      if (n && (it.entityType || "record") === "record" && !pool[n])
        pool[n] = { type: (it.secondaryText || "").trim() || null, id: it.entityID };
    }
  };
  harvest(firstData);
  // A dropped connection or a server error gets a few more tries. A refusal gets one, after a pause in case it was a
  // blip; if the portal still says no, the run stops searching (and goes on to read permit pages).
  async function searchWithRetry(key, retries = 3) {
    for (let attempt = 0; ; attempt++) {
      try { return await search(key); }
      catch (e) {
        const refused = REFUSED.test(e.message);
        if (attempt >= (refused ? 1 : retries) || stopped) throw e;
        const wait = refused ? 60 : [30, 60, 120][attempt];
        status(refused ? `The portal refused a search (${e.message}). Waiting ${wait} seconds, then trying once more…`
          : `Connection hiccup (${e.message}). Waiting ${wait} seconds, then trying again…`);
        log(`${key}: ${e.message}, retrying in ${wait}s`);
        await sleep(wait * 1000);
      }
    }
  }
  async function get(number) {
    if (permits[number]) return permits[number];
    if (!pool[number] && !searched.has(number)) {
      searched.add(number);
      await sleep(PAUSE);
      const data = await searchWithRetry(number);
      searches++;
      harvest(data);
    }
    return pool[number];
  }

  // Permit pages stopped loading: the run stops instead of pushing on. What's read so far stays saved in this browser
  // and goes to the site with the next run that works.
  function stopRun(why) {
    save();
    status(`Stopped: ${why}. Nothing is lost; try again later, for example tomorrow.`);
    log(`Stopped: ${why}`);
    $("gvz-dl").disabled = false;
    window.GVZ_RESULT = { error: why };
  }

  // Keyword searches for code enforcement records; any series they turn up gets walked like the others
  if (!searchRefused && due("words:at")) {
    let ok = true;
    for (const word of ENFORCE_WORDS) {
      if (stopped) break;
      status(`Looking for code enforcement records… (${word})`);
      try { await sleep(PAUSE); harvest(await searchWithRetry(word, 0)); searches++; }
      catch (e) { ok = false; log(`"${word}" search: ${e.message}`); if (REFUSED.test(e.message)) { searchWasRefused(e.message); break; } }
    }
    const enforce = Object.entries(pool).filter(([, v]) => ENFORCE_TYPE.test(v.type || ""));
    for (const [n] of enforce) { const m = numRe.exec(n); if (m && !PREFIXES.includes(m[1].toUpperCase())) PREFIXES.push(m[1].toUpperCase()); }
    log(enforce.length ? `Code enforcement: found ${enforce.length} record(s), types: ${[...new Set(enforce.map(([, v]) => v.type))].join(", ")}`
      : "Code enforcement: none found by keyword search");
    if (ok && !stopped) { done["words:at"] = Date.now(); saveDone(); }
  }
  const thisYY = new Date().getFullYear() % 100;
  const YEARS = Array.from({ length: YEARS_BACK + 1 }, (_, i) => String(thisYY - i).padStart(2, "0"));
  const now = new Date().toISOString().slice(0, 19) + "+00:00";
  let added = 0;
  const known = (prefix, yy) => Object.keys(permits).concat(Object.keys(pool)).some((n) => n.startsWith(`${prefix}-${yy}-`));

  try {
   for (const yy of YEARS) {
    if (searchRefused || stopped) break;
    const past = yy !== YEARS[0];
    // this year's series are walked again for new permits, at most every 12 hours
    if (!past && !due(yy + ":walkedAt")) { log(`20${yy}: looked for new permits in the last 12 hours, so not again yet`); continue; }
    let prefixes = past ? done[yy + ":prefixes"] : null;
    // past years remember which series were checked, so newly added series still get checked once
    const checked = past ? (done[yy + ":checked"] || (prefixes ? OLD_PREFIXES : [])) : [];
    const toCheck = PREFIXES.filter((p) => !checked.includes(p));
    if (!prefixes || toCheck.length) {  // which series exist this year?
      prefixes = prefixes ? prefixes.slice() : [];
      for (const prefix of toCheck) {
        if (stopped) break;
        status(`Looking for 20${yy} permit series… (${prefix})`);
        // Searches are fuzzy, so a search for #1 also returns other numbers in the series.
        // A series counts as existing if any of its numbers show up, even when #1-3 were voided.
        let exists = known(prefix, yy);
        for (const s of [1, 2, 3]) { if (exists) break; await get(`${prefix}-${yy}-${s}`); exists = known(prefix, yy); }
        if (exists) prefixes.push(prefix);
      }
      if (past && !stopped) { done[yy + ":prefixes"] = prefixes; done[yy + ":checked"] = PREFIXES; saveDone(); }
      if (past) log(`20${yy}: ${prefixes.length ? prefixes.join(", ") : "no permits found"}`);
    }
    for (const prefix of prefixes) {
      if (stopped) break;
      const series = `${prefix}-${yy}`;
      if (done[series]) continue;
      status(`Checking ${series} permits…`);
      let n = Math.max(0, ...Object.keys(permits).filter((k) => k.startsWith(series + "-")).map((k) => +k.split("-").pop())) + 1;
      let misses = 0, before = added;
      // highest number we've seen anywhere in this series; keep walking at least that far
      const seenMax = () => Math.max(0, ...Object.keys(pool).concat(Object.keys(permits))
        .filter((k) => k.startsWith(series + "-")).map((k) => +k.split("-").pop()));
      while ((misses < LOOKAHEAD || n <= seenMax()) && !stopped) {
        const number = `${series}-${n}`;
        const hit = await get(number);
        if (hit && !permits[number]) {
          permits[number] = { number, type: hit.type, entity_id: hit.id,
            url: `${location.origin}/records/${hit.id}`, address: null, date: null,
            lat: null, lon: null, first_seen: now, details: false };
          added++;
        }
        misses = hit ? 0 : misses + 1;
        n++;
      }
      save();
      if (past && !stopped) { done[series] = true; saveDone(); }
      log(`${series}: ${added - before} new, up to #${n - LOOKAHEAD - 1}`);
    }
    if (!past && !stopped) { done[yy + ":walkedAt"] = Date.now(); saveDone(); }
   }
  } catch (e) {
    save();
    if (REFUSED.test(e.message)) searchWasRefused(e.message);
    log(`Search stopped: ${e.message}. Progress is saved; run the script again later to continue.`);
  }
  // keep enforcement records the keyword searches found outside the walked series (added after the walk,
  // since a series resumes after the highest number already saved)
  for (const [number, hit] of Object.entries(pool)) {
    if (ENFORCE_TYPE.test(hit.type || "") && !permits[number]) {
      permits[number] = { number, type: hit.type, entity_id: hit.id, url: `${location.origin}/records/${hit.id}`,
        address: null, date: null, lat: null, lon: null, first_seen: now, details: false };
      added++;
    }
  }
  save();
  log(`${added} new permits from ${searches} searches`);

  // ---------- addresses (from each record's page) ----------
  const street = "(?:Ave(?:nue)?|St(?:reet)?|Rd|Road|Blvd|Boulevard|Dr(?:ive)?|Ct|Court|Pl(?:ace)?|Way|Ln|Lane|Pkwy|Parkway|Cir(?:cle)?|Ter(?:race)?)";
  const addrRe = new RegExp(`\\b\\d{1,5}(?:-\\d{1,5})?\\s+(?:[NSEW]\\.?\\s+)?(?:[A-Za-z0-9']+\\.?\\s+){1,3}?${street}\\b\\.?`, "i");
  const cityHall = /^1525\s+(W\.?\s+)?Goodale/i;
  const cleanAddr = (a) => a.replace(/\s+/g, " ").replace(/,?\s*(Grandview Heights|Columbus)?,?\s*OH(io)?\b.*$/i, "").replace(/^[\s,.]+|[\s,.]+$/g, "");
  const toDate = (v) => {
    if (typeof v === "number" && v > 1e11) return new Date(v).toISOString().slice(0, 10);
    let m = /(20\d\d-\d\d-\d\d)/.exec(v || ""); if (m) return m[1];
    m = /\b(\d{1,2})\/(\d{1,2})\/(20\d\d)\b/.exec(v || "");
    return m ? `${m[3]}-${m[1].padStart(2, "0")}-${m[2].padStart(2, "0")}` : null;
  };
  function flatten(o, p = "", out = {}) {
    if (o && typeof o === "object") for (const [k, v] of Object.entries(o)) flatten(v, p + k + ".", out);
    else if (o !== null && o !== "") out[p.slice(0, -1)] = o;
    return out;
  }
  function fromJson(data) {
    const flat = flatten(data);
    let address = null, date = null;
    for (const [k, v] of Object.entries(flat)) {
      const leaf = k.split(".").pop().toLowerCase();
      if (!address && typeof v === "string" && /address|location|street/.test(leaf) && addrRe.test(v) && !cityHall.test(v))
        address = cleanAddr(addrRe.exec(v)[0]);
      if (!date && /submit|creat|applied|dateopen|issued/.test(leaf)) date = toDate(v);
    }
    if (!address) {
      const no = Object.entries(flat).find(([k]) => /street(no|number)|houseno/i.test(k));
      const nm = Object.entries(flat).find(([k]) => /streetname/i.test(k));
      if (no && nm) address = cleanAddr(`${no[1]} ${nm[1]}`);
    }
    return { address, date, contractors: contractorsFrom(flat) };
  }
  // ---------- contractors: the companies named on each permit ----------
  // Names come from contractor/company fields, and from business names (LLC, Inc., Plumbing, Roofing...) in other
  // fields or the page text. Applicant and contact fields count only when the name is clearly a business, so
  // homeowners pulling their own permits aren't listed.
  const bizInText = new RegExp(`((?:[A-Z0-9&][\\w&'.-]*[ \\t]+){0,6}(?:LLC|L\\.L\\.C\\.?|Inc\\.?|Corp\\.?|Company|Co\\.|Ltd\\.?))(?=[\\s,.;)]|$)`, "g");
  const notName = /@|https?:|^\d|\d{3}[-. )]\d{3}|^(yes|no|true|false|n\/a|none|null|self|owner|homeowner)$/i;
  // form labels and headings that aren't names ("Name *", "Contractor Information", "Company Name Primary Contact ...")
  const isLabel = (n) => /\*\s*$|\+\s*\d+\s+additional|primary contact/i.test(n) || /^(information|contractor information|(company|business|contractor)?\s*name|address|city|state|zip|email|phone|company|contractor|occupancy type|type of .*|building permit number|residential or commercial.*|inc|llc|co|corp|ltd)\.?$/i.test(n.trim());
  const tidy = (n) => String(n).replace(/\s+/g, " ").replace(/^[\s,.;:-]+|[\s,;:-]+$/g, "").slice(0, 80);
  function contractorsFrom(flat) {
    const out = new Map();
    const add = (n) => { n = cleanName(tidy(n)); if (n.length >= 3 && !notName.test(n) && !isLabel(n) && !cityHall.test(n) && !looksLikePerson(n)) out.set(n.toUpperCase().replace(/[.,]/g, ""), n); };
    for (const [k, v] of Object.entries(flat)) {
      if (typeof v !== "string") continue;
      const key = k.toLowerCase();
      if (/contractor|company|business|licens|firm|organi[sz]ation|vendor/.test(key) && !/type|id$|number|date|status|email|phone|address|street|city|zip|state/.test(key)) add(v);
      else if (/applicant|contact|name/.test(key) && !/first|last|middle|user/.test(key) && bizRe.test(v) && v.length < 80) add(v);
      for (const m of v.matchAll(bizInText)) add(m[1]);
    }
    return [...out.values()].slice(0, 6);
  }
  // The record page shows its application form; values sit inside form fields, which the page's text leaves out.
  // Walk the page in order, remembering the current section heading, and read each field's label and value.
  function formFields(doc) {
    const out = [];
    let section = "";
    const labelOf = (el) => {
      let l = "";
      if (el.id) { const f = doc.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (f) l = f.textContent; }
      if (!l) l = el.getAttribute("aria-label") || "";
      for (let n = el.parentElement, i = 0; !l && n && i < 4; n = n.parentElement, i++) {
        const f = n.querySelector("label, legend, [class*='label' i]"); if (f && !f.contains(el)) l = f.textContent; }
      return tidy(l || el.getAttribute("placeholder") || "").replace(/\s*\*$/, "");
    };
    for (const el of doc.body.querySelectorAll("*")) {
      const own = el.children.length === 0 ? (el.textContent || "").trim() : "";
      if (/^(H[1-6]|LEGEND)$/.test(el.tagName) && el.textContent && el.textContent.length < 80) section = tidy(el.textContent);
      else if (own && own.length < 60 && (/information$/i.test(own) || /^(contractor|applicant|owner|contact|licensed professional)s?\b/i.test(own))
        && !el.closest("label")) section = tidy(own);
      else if (/^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName) && !/hidden|checkbox|radio|button|submit/.test(el.type || "")) {
        const value = el.tagName === "SELECT" ? ((el.selectedOptions && el.selectedOptions[0]) || {}).text || "" : el.value || "";
        out.push({ section, label: labelOf(el), value: tidy(value) });
      }
    }
    return out;
  }
  function contractorsFromFields(fields) {
    // The company is the answer to "Company Name" / "Business Name". A contractor's own name ("Contractor Name", or a
    // name in a contractor section) counts only when its section names no company, and the applicant's account name
    // (the line under "Applicant" at the top of the page) only when nothing else does. A person's name never counts.
    const OTHER = /type|number|license|phone|email|address|city|state|zip|contact|website|fax|owner/;
    const out = new Map(), applicant = [];
    const add = (n) => {
      n = cleanName(tidy(n));
      if (n.length < 3 || notName.test(n) || isLabel(n) || looksLikeType(n) || looksLikePerson(n)) return false;
      out.set(n.toUpperCase().replace(/[.,]/g, ""), n);
      return true;
    };
    for (const secName of [...new Set(fields.map((f) => f.section))]) {
      const fs = fields.filter((f) => f.section === secName && f.value), sec = secName.toLowerCase();
      if (/owner/.test(sec)) continue;   // the property owner (or a business tenant) isn't who did the work
      const lab = (f) => f.label.toLowerCase(), isName = (f) => /^(name|full name)$/.test(lab(f));
      const company = fs.filter((f) => /company|business|firm/.test(lab(f)) && !OTHER.test(lab(f)));
      const contractor = fs.filter((f) => (/contractor/.test(lab(f)) && !OTHER.test(lab(f))) || (isName(f) && /contractor|licensed|professional/.test(sec)));
      if (!company.map((f) => add(f.value)).some(Boolean)) contractor.forEach((f) => add(f.value));
      if (/applicant|contact/.test(sec)) applicant.push(...fs.filter((f) => isName(f) && tradeRe.test(f.value)));
    }
    if (!out.size) applicant.forEach((f) => add(f.value));
    return [...out.values()].slice(0, 6);
  }
  // The portal's record page shows each question with its answer on the next line, under section headings:
  //   Contractor Information / Company Name * / CMC Plumbing & Drain / Primary Contact Name * / ...
  function fieldsFromText(text) {
    const lines = text.split("\n").map((l) => l.trim()).filter(Boolean), out = [];
    const isQ = (l) => /\*\s*$/.test(l);
    let section = "";
    for (let i = 0; i < lines.length; i++) {
      const l = lines[i];
      if (/information$/i.test(l) && l.length < 60 && !isQ(l)) { section = tidy(l); continue; }
      if (i < 12 && /^applicant$/i.test(l) && lines[i + 1] && !isQ(lines[i + 1])) { out.push({ section: "Applicant", label: "Name", value: tidy(lines[i + 1]) }); continue; }
      if (!isQ(l)) continue;
      let j = i + 1;
      while (j < lines.length && /^toggle tooltip$/i.test(lines[j])) j++;
      const next = lines[j] || "";
      const answered = next && !isQ(next) && !/information$/i.test(next);
      out.push({ section, label: tidy(l).replace(/\s*\*$/, ""), value: answered ? tidy(next) : "" });
    }
    return out;
  }
  function contractorsFromText(text) {
    const out = new Map();
    for (const m of text.matchAll(bizInText)) { const n = cleanName(tidy(m[1])); if (n.length >= 3 && !isLabel(n) && !looksLikePerson(n)) out.set(n.toUpperCase().replace(/[.,]/g, ""), n); }
    return [...out.values()].slice(0, 6);
  }
  // Each record page is a full web app, and loading hundreds of them crashed the tab ("Aw, Snap!"). So: open ONE
  // record page, note which data addresses it fetched for that record, then fetch just that data for every permit.
  let recordApis = null, framesThisRun = 0;
  async function learnRecordApis(p) {
    recordApis = [];
    const frame = recordFrame();
    frame.src = `/records/${p.entity_id}`;
    // Copy the request headers the record page itself sends to the data service (its current access pass): as soon
    // as the page's window exists, before its code runs, wrap its request functions to note them.
    const captured = {}, seenUrls = new Set();
    const API_RE = /viewpointcloud|\/v2\//i;
    const hook = setInterval(() => {
      try {
        const w = frame.contentWindow;
        if (!w || w.__gvzHooked || !/\/records\//.test(w.location.href)) return;
        w.__gvzHooked = true;
        // the browser's list of loaded resources holds 250 by default; the portal can load more than that
        try { w.performance.setResourceTimingBufferSize(3000); } catch (e) {}
        const X = w.XMLHttpRequest.prototype, open = X.open, setH = X.setRequestHeader, f = w.fetch;
        X.open = function (m, u) { this.__gvzU = String(u); if (API_RE.test(this.__gvzU)) seenUrls.add(new URL(this.__gvzU, w.location.href).href); return open.apply(this, arguments); };
        X.setRequestHeader = function (k, v) { if (API_RE.test(this.__gvzU || "") && !FORBIDDEN.test(k)) captured[k.toLowerCase()] = v; return setH.apply(this, arguments); };
        w.fetch = function (input, init) {
          const u = String((input && input.url) || input);
          if (API_RE.test(u)) { try { seenUrls.add(new URL(u, w.location.href).href); } catch (e) {} }
          if (API_RE.test(u)) new w.Headers((init && init.headers) || (input && input.headers) || {}).forEach((v, k) => { if (!FORBIDDEN.test(k)) captured[k.toLowerCase()] = v; });
          return f.apply(w, arguments);   // must run as the page's own fetch
        };
      } catch (e) {}
    }, 5);
    for (let i = 0; i < 40; i++) {
      await sleep(500);
      let text = ""; try { text = frame.contentDocument.body.innerText || ""; } catch (e) { break; }
      if (text.includes(p.number.split("-").pop()) && addrRe.test(text)) { await sleep(2500); break; }
    }
    clearInterval(hook);
    if (Object.keys(captured).length) {
      headers = { ...headers, ...captured };
      localStorage.setItem(STORE + "-headers", JSON.stringify(headers));
      log(`Using the portal's current access headers (${Object.keys(captured).join(", ")})`);
    } else log("The record page sent no special headers");
    let urls = [...seenUrls];
    try { urls = urls.concat(frame.contentWindow.performance.getEntriesByType("resource").map((e) => e.name)); } catch (e) {}
    releaseFrame(true);
    const id = String(p.entity_id);
    recordApis = [...new Set(urls.filter((u) => u.includes(id) && !/\.(js|css|png|jpe?g|svg|woff2?|ico)(\?|$)/i.test(u))
      .map((u) => u.split(id).join("{id}")))].slice(0, 10);
    log(recordApis.length ? `Found ${recordApis.length} data addresses for a permit; reading permits from those` : "Couldn't find the permit data addresses; reading pages instead");
  }
  // label/value pairs anywhere in the record data, with the nearest enclosing heading as the section
  function fieldsFromJson(o, section = "", out = []) {
    if (Array.isArray(o)) { o.forEach((x) => fieldsFromJson(x, section, out)); return out; }
    if (!o || typeof o !== "object") return out;
    const lab = ["label", "name", "title", "displayName", "fieldName", "question"].map((k) => o[k]).find((v) => typeof v === "string" && v.length < 80);
    const val = ["value", "answer", "response", "displayValue", "text"].map((k) => o[k]).find((v) => typeof v === "string" || typeof v === "number");
    if (lab && val !== undefined) out.push({ section, label: tidy(lab).replace(/\s*\*$/, ""), value: tidy(String(val)) });
    const sec = lab && val === undefined && Object.values(o).some((v) => v && typeof v === "object") ? tidy(lab) : section;
    for (const v of Object.values(o)) if (v && typeof v === "object") fieldsFromJson(v, sec, out);
    return out;
  }
  // The portal keeps a record's form in separate lists: sections (headings), fields (labels, each in a section) and
  // entries (values, each pointing at a field by its id). Join them back into section / label / value rows.
  const rowsOf = (j) => (Array.isArray(j) ? j : j && Array.isArray(j.data) ? j.data : j && j.data ? [j.data] : [])
    .filter((o) => o && typeof o === "object").map((o) => ({ id: o.id, ...(o.attributes || o) }));
  const pick = (o, re) => { for (const [k, v] of Object.entries(o)) if (re.test(k) && v !== null && v !== "" && typeof v !== "object") return v; };
  const valueOf = (o) => {
    let v = pick(o, /^(value|entry|displayValue|response|text|valueText|answer)$/i);
    if (v === undefined) for (const [k, x] of Object.entries(o)) if (/^(value|values|displayValue)$/i.test(k) && x && typeof x === "object")
      v = [].concat(x).map((y) => (y && typeof y === "object" ? y.label || y.name || y.value || "" : y)).filter(Boolean).join(", ");
    return v;
  };
  function formRows(parts) {
    const list = (name) => parts.filter((x) => x.name === name).flatMap((x) => rowsOf(x.json));
    const sections = new Map(list("form_sections").map((o) => [String(o.id), pick(o, /^(label|name|title|sectionLabel|sectionName|heading)$/i) || ""]));
    const fieldDefs = new Map(list("form_fields").map((o) => [String(o.id), {
      label: pick(o, /^(label|name|title|fieldLabel|question|displayName)$/i) || "",
      section: pick(o, /form_?section_?id$|^section_?id$/i) }]));
    const out = [];
    for (const e of list("form_field_entries").concat(list("multi_entry_item_entries"))) {
      const fid = pick(e, /form_?field_?id$|^field_?id$/i), f = fieldDefs.get(String(fid));
      const v = valueOf(e);
      if (!f || v === undefined) continue;
      out.push({ section: tidy(sections.get(String(f.section)) || ""), label: tidy(f.label).replace(/\s*\*$/, ""), value: tidy(String(v)) });
    }
    return out;
  }
  // The portal's access pass, if the app keeps one in this site's storage (a token shaped like eyJ....).
  function storedToken() {
    let best = "";
    for (const store of [localStorage, sessionStorage]) {
      try {
        for (let i = 0; i < store.length; i++) {
          for (const m of String(store.getItem(store.key(i)) || "").matchAll(/eyJ[\w-]{10,}\.[\w-]{10,}\.[\w-]{10,}/g)) if (m[0].length > best.length) best = m[0];
        }
      } catch (e) {}
    }
    return best;
  }
  // Ways to ask a data list, tried in order when one is refused: the saved headers; with this browser's cookies;
  // with the stored access pass; both. The first that works is remembered for that list.
  const ASK_WAYS = ["plain", "cookies", "token", "token+cookies"];
  const askWay = {};
  async function ask(url, name) {
    const token = storedToken();
    let last = null;
    for (const way of askWay[name] ? [askWay[name]] : ASK_WAYS) {
      if (/token/.test(way) && !token) continue;
      const h = /token/.test(way) ? { ...headers, authorization: `Bearer ${token}` } : headers;
      try {
        const r = await fetch(url, { headers: h, ...(/cookies/.test(way) ? { credentials: "include" } : {}) });
        last = r.status;
        if (r.ok) { askWay[name] = way; return { status: `${r.status}${way === "plain" ? "" : ` (${way})`}`, json: await r.json() }; }
        if (r.status !== 401 && r.status !== 403) break;
      } catch (e) { last = String(e.message || e).slice(0, 40); }
    }
    return { status: last, json: null };
  }
  async function fromApis(p) {
    const status = {};
    const parts = (await Promise.all(recordApis.map(async (t) => {
      const name = t.split("?")[0].split("/").pop();
      const { status: st, json } = await ask(t.split("{id}").join(p.entity_id), name);
      status[name] = st;
      return json ? { name, json } : null;
    }))).filter(Boolean);
    if (!recordStatus) { recordStatus = status; log(`Data lists: ${Object.entries(status).map(([k, v]) => `${k} ${v}`).join(", ")}`); }
    if (!parts.length) return null;
    if (!recordShapes) recordShapes = Object.fromEntries(parts.map((x) => { const r = rowsOf(x.json)[0];
      return [x.name, r ? Object.keys(r).slice(0, 40) : Object.keys(x.json || {}).slice(0, 20)]; }));
    const jsons = parts.map((x) => x.json);
    if (!recordFields) recordFields = Object.keys(flatten(jsons)).slice(0, 400);
    const joined = formRows(parts);
    const d = fromJson(jsons), fields = joined.length ? joined : fieldsFromJson(jsons);
    if (!recordForm && fields.length) recordForm = fields.map((f) => ({ section: f.section, label: f.label, filled: !!f.value }));
    const fromFields = contractorsFromFields(fields);
    return { ...d, parsed: joined.length > 0, contractors: joined.length || fromFields.length ? fromFields : d.contractors };
  }
  async function details(p) {
    if (recordApis === null) {
      // the data lists are tried at most every 12 hours; when they last didn't return the form, read pages straight away
      if (due("lists:failedAt")) await learnRecordApis(p);
      else { recordApis = []; log("Reading each permit's page (the data lists didn't return the form in the last 12 hours)"); }
    }
    if (recordApis.length) {
      const d = await fromApis(p);
      // the data lists count only when they return the permit's form; otherwise read the page, which shows it
      if (d && d.parsed) return d;
      recordApis = [];
      done["lists:failedAt"] = Date.now(); saveDone();
      log("The data lists didn't return the permit's form; reading each permit's page instead");
    }
    // read the page itself, a limited number per batch so the tab stays healthy
    if (framesThisRun >= FRAME_LIMIT) return null;
    framesThisRun++;
    return fromFrame(p);
  }
  // One hidden frame for all record pages: blanked after each page and rebuilt every FRAME_RECYCLE pages, since each
  // page is a full web app and opening hundreds of separate frames made the tab crash ("Aw, Snap!")
  let frameEl = null, framePages = 0;
  function recordFrame() {
    if (!frameEl) {
      frameEl = document.createElement("iframe");
      frameEl.style.cssText = "position:fixed;left:-9999px;width:1200px;height:900px";
      document.body.appendChild(frameEl);
    }
    return frameEl;
  }
  function releaseFrame(drop) {
    if (!frameEl) return;
    framePages++;
    if (drop || framePages % FRAME_RECYCLE === 0) { frameEl.src = "about:blank"; frameEl.remove(); frameEl = null; }
    else frameEl.src = "about:blank";
  }
  async function fromFrame(p) {
    // Load the record page in the hidden frame and read it
    const frame = recordFrame();
    frame.src = `/records/${p.entity_id}`;
    let text = "", fields = [];
    for (let i = 0; i < 30; i++) {
      await sleep(500);
      try { text = frame.contentDocument.body.innerText || ""; } catch (e) { break; }
      if (addrRe.test(text) && text.includes(p.number.split("-").pop())) break;
    }
    try { await sleep(800); fields = formFields(frame.contentDocument); } catch (e) { fields = []; }
    releaseFrame();
    const label = /(Location|Address|Property)\s*:?\s*\n?/i.exec(text);
    const region = label ? text.slice(label.index + label[0].length, label.index + label[0].length + 300) : text;
    const m = [...region.matchAll(new RegExp(addrRe.source, "gi"))].find((x) => !cityHall.test(x[0]));
    const dm = /(Submitted|Applied|Created|Date)\D{0,20}(\d{1,2}\/\d{1,2}\/20\d\d)/i.exec(text);
    const textFields = fieldsFromText(text);
    // one form layout per permit type (questions and section headings, never the answers) goes in the upload to help
    // tune this; a heading is kept only when it reads like one ("Contractor Information"), so no answer can slip in
    const heading = (t) => (/^([A-Z][\w/&'-]*\s+){0,5}Information$/.test(t) ? t : "");
    const layout = (textFields.length ? textFields : fields).slice(0, 60).map((f) => ({ section: heading(f.section), label: f.label, filled: !!f.value }));
    if (layout.length && p.type && !recordForms[p.type]) recordForms[p.type] = layout;
    if (!recordForm && layout.length) recordForm = layout;
    // When the page reads as questions and answers, that decides: an empty contractor section means no contractor.
    // Its hidden form widgets (they hold things like the permit type) and stray business names (the property owner
    // or a tenant) are used only for pages that don't read that way.
    const parsed = textFields.length > 0;
    // no questions, no address and no permit number: the page never showed the permit (refused, or still loading)
    const showsNumber = text.toUpperCase().replace(/-0+(?=\d)/g, "-").includes(String(p.number).toUpperCase());
    if (!parsed && !m && !showsNumber) return { failed: true };
    const fromFields = contractorsFromFields(parsed ? textFields : fields);
    return { address: m ? cleanAddr(m[0]) : null, date: dm ? toDate(dm[2]) : null, parsed,
      contractors: parsed || fromFields.length ? fromFields : contractorsFromText(text) };
  }

  // new permits, plus ones read before contractors were collected
  const todo = Object.values(permits).filter((p) => (!p.details || p.cv !== CONTRACTOR_VERSION) && p.entity_id)
    .sort((a, b) => b.entity_id - a.entity_id).slice(0, MAX_DETAILS);
  let found = 0, withContractor = 0, read = 0, failedInARow = 0, pagesStopped = null;
  $("gvz-dl").disabled = false;   // what's done so far can be downloaded any time
  for (const [i, p] of todo.entries()) {
    if (stopped) break;
    status(`Reading addresses and contractors: ${i + 1} of ${todo.length}`);
    try {
      await sleep(PAGE_PAUSE);
      const d = await details(p);
      if (!d) { log(`Read ${FRAME_LIMIT} full pages this run, the most that's safe at once. Reload the portal and run again to continue.`); break; }
      // A page that didn't load isn't counted as read, so it's tried again next time. Three in a row means the portal
      // has stopped showing pages, so the run stops instead of asking for more.
      if (d.failed) {
        log(`${p.number}: the page didn't load`);
        if (++failedInARow >= 3) { pagesStopped = "permit pages stopped loading"; break; }
        continue;
      }
      failedInARow = 0; read++;
      // A page that read cleanly decides who the contractor is (none, if its contractor section is empty). Only when a
      // read failed does an earlier read's list stand, minus any permit types an older version mistook for names.
      const real = (list) => (list || []).map(cleanName).filter((n) => !looksLikeType(n) && !looksLikePerson(n) && n.toLowerCase() !== String(p.type || "").toLowerCase());
      const contractors = d.parsed ? real(d.contractors) : (real(d.contractors).length ? real(d.contractors) : real(p.contractors));
      Object.assign(p, { address: d.address || p.address, date: d.date || p.date, details: true, contractors, cv: CONTRACTOR_VERSION });
      if (d.address) found++;
      if (d.contractors && d.contractors.length) withContractor++;
      if (read === 1) log(`${p.number}: ${d.address || "no address found"}${d.date ? ", filed " + d.date : ""}${d.contractors && d.contractors.length ? ", " + d.contractors.join(", ") : ""}`);
      if (i % 5 === 4) save();
    } catch (e) { log(`${p.number}: ${e.message}`); }
  }
  save();
  if (frameEl) { frameEl.remove(); frameEl = null; }
  // Pages stopped loading. Nothing read: the run stops. Some read: they're handed over as usual, and the next batch
  // stops if pages still don't load.
  if (pagesStopped && !read) return stopRun(pagesStopped);
  if (pagesStopped) log(`Stopped this batch early: ${pagesStopped}`);
  const left = Object.values(permits).filter((p) => !p.details || p.cv !== CONTRACTOR_VERSION).length;
  log(`Addresses: ${found} of ${todo.length} found; contractors on ${withContractor}${left ? `; ${left} left for next time` : ""}`);
  status(`Done. ${Object.keys(permits).length} permits${left ? ` (run again later for ${left} more)` : ""}. Click Download.`);
  $("gvz-dl").disabled = false;
  $("gvz-stop").remove();
  // "processed" is how many this batch actually read; the Chrome extension ends a run when a batch reads none
  window.GVZ_RESULT = { json: payload(), total: Object.keys(permits).length, processed: read, left, withContractor };
})();
void 0;   // the script's result is "nothing", so the Chrome extension doesn't wait for the whole run to finish
