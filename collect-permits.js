// Grandview permit collector
// 1. Open https://grandviewheightsoh.portal.opengov.com/search
// 2. Open the browser console (right-click > Inspect > Console)
// 3. Paste this whole file and press Enter
// A panel appears in the corner. When it says "Done", click Download and upload
// the file to docs/permits.json in the grandview-zoning repo.
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
  const MAX_DETAILS = 300;   // record pages per click; run again to continue
  const YEARS_BACK = 3;      // also collect this many past years (finished years are only walked once)
  const PAUSE = 1500;        // ms between requests
  const STORE = "gvz-permits-v1";

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
  const status = (t) => { $("gvz-status").textContent = t; };
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
  } else log(`Loaded ${Object.keys(permits).length} permits saved in this browser`);
  const save = () => localStorage.setItem(STORE, JSON.stringify(permits));

  function download() {
    const list = Object.values(permits).sort((a, b) =>
      ((b.date || b.first_seen.slice(0, 10)) + String(b.entity_id).padStart(9, "0"))
        .localeCompare((a.date || a.first_seen.slice(0, 10)) + String(a.entity_id).padStart(9, "0")));
    const blob = new Blob([JSON.stringify({ updated: new Date().toISOString(), permits: list }, null, 2)],
      { type: "application/json" });
    const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(blob), download: "permits.json" });
    document.body.appendChild(a); a.click(); a.remove();
  }
  $("gvz-dl").onclick = download;

  // ---------- API ----------
  let headers = {};
  try { headers = JSON.parse(localStorage.getItem(STORE + "-headers") || "{}"); } catch (e) {}
  const FORBIDDEN = /^(host|origin|referer|user-agent|cookie|connection|content-length|priority|if-none-match|if-modified-since|accept-encoding|sec-)/i;

  function captureHeaders() {
    return new Promise((resolve) => {
      const keep = (h) => { const o = {}; for (const [k, v] of Object.entries(h)) if (!FORBIDDEN.test(k)) o[k] = v; resolve(o); };
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

  // Make sure searching works; if not, borrow the headers the portal's own search uses
  let firstData = null;
  try { firstData = await search("R-26-82"); }
  catch (e) {
    status("One step from you: type R-26-82 in the portal's search box and press Enter.");
    log(`Direct search failed (${e.message}); waiting for you to search once`);
    headers = await captureHeaders();
    localStorage.setItem(STORE + "-headers", JSON.stringify(headers));
    await sleep(1500);
    try { firstData = await search("R-26-82"); log("Got it, searching works now"); }
    catch (e2) { status(`The permit search refused requests (${e2.message}). Nothing changed.`); return; }
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
  async function get(number) {
    if (permits[number]) return permits[number];
    if (!pool[number] && !searched.has(number)) {
      searched.add(number);
      await sleep(PAUSE);
      let data;
      for (let attempt = 0; ; attempt++) {
        try { data = await search(number); break; }
        catch (e) {  // a blip or the server asking us to slow down: wait, then retry
          if (attempt >= 3 || stopped) throw e;
          const wait = [30, 60, 120][attempt];
          status(`Connection hiccup (${e.message}). Waiting ${wait} seconds, then trying again…`);
          log(`${number}: ${e.message}, retrying in ${wait}s`);
          await sleep(wait * 1000);
        }
      }
      searches++;
      harvest(data);
    }
    return pool[number];
  }

  // Keyword searches for code enforcement records; any series they turn up gets walked like the others
  for (const word of ENFORCE_WORDS) {
    if (stopped) break;
    status(`Looking for code enforcement records… (${word})`);
    try { await sleep(PAUSE); harvest(await search(word)); searches++; } catch (e) { log(`"${word}" search: ${e.message}`); }
  }
  const enforce = Object.entries(pool).filter(([, v]) => ENFORCE_TYPE.test(v.type || ""));
  for (const [n] of enforce) { const m = numRe.exec(n); if (m && !PREFIXES.includes(m[1].toUpperCase())) PREFIXES.push(m[1].toUpperCase()); }
  log(enforce.length ? `Code enforcement: found ${enforce.length} record(s), types: ${[...new Set(enforce.map(([, v]) => v.type))].join(", ")}`
    : "Code enforcement: none found by keyword search");
  const thisYY = new Date().getFullYear() % 100;
  const YEARS = Array.from({ length: YEARS_BACK + 1 }, (_, i) => String(thisYY - i).padStart(2, "0"));
  const now = new Date().toISOString().slice(0, 19) + "+00:00";
  let added = 0;
  let done = {};
  try { done = JSON.parse(localStorage.getItem(STORE + "-done") || "{}"); } catch (e) {}
  const saveDone = () => localStorage.setItem(STORE + "-done", JSON.stringify(done));
  const known = (prefix, yy) => Object.keys(permits).concat(Object.keys(pool)).some((n) => n.startsWith(`${prefix}-${yy}-`));

  try {
   for (const yy of YEARS) {
    const past = yy !== YEARS[0];
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
   }
  } catch (e) { save(); log(`Search stopped: ${e.message}. Progress is saved; run the script again later to continue.`); }
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
    return { address, date };
  }
  let jsonWorks = true;
  async function details(p) {
    if (jsonWorks) {
      try {
        const r = await fetch(`${API}/records/${p.entity_id}`, { headers });
        if (r.ok) { const d = fromJson(await r.json()); if (d.address) return d; }
        else if (r.status === 404) jsonWorks = false;
      } catch (e) { jsonWorks = false; }
    }
    // Fall back to loading the record page in a hidden frame and reading its text
    const frame = Object.assign(document.createElement("iframe"), { src: `/records/${p.entity_id}` });
    frame.style.cssText = "position:fixed;left:-9999px;width:1200px;height:900px";
    document.body.appendChild(frame);
    let text = "";
    for (let i = 0; i < 30; i++) {
      await sleep(500);
      try { text = frame.contentDocument.body.innerText || ""; } catch (e) { break; }
      if (addrRe.test(text) && text.includes(p.number.split("-").pop())) break;
    }
    frame.remove();
    const label = /(Location|Address|Property)\s*:?\s*\n?/i.exec(text);
    const region = label ? text.slice(label.index + label[0].length, label.index + label[0].length + 300) : text;
    const m = [...region.matchAll(new RegExp(addrRe.source, "gi"))].find((x) => !cityHall.test(x[0]));
    const dm = /(Submitted|Applied|Created|Date)\D{0,20}(\d{1,2}\/\d{1,2}\/20\d\d)/i.exec(text);
    return { address: m ? cleanAddr(m[0]) : null, date: dm ? toDate(dm[2]) : null };
  }

  const todo = Object.values(permits).filter((p) => !p.details && p.entity_id)
    .sort((a, b) => b.entity_id - a.entity_id).slice(0, MAX_DETAILS);
  let found = 0;
  for (const [i, p] of todo.entries()) {
    if (stopped) break;
    status(`Reading addresses: ${i + 1} of ${todo.length}`);
    try {
      await sleep(PAUSE);
      const d = await details(p);
      Object.assign(p, { address: d.address, date: d.date || p.date, details: true });
      if (d.address) found++;
      if (i === 0) log(`${p.number}: ${d.address || "no address found"}${d.date ? ", filed " + d.date : ""}`);
      save();
    } catch (e) { log(`${p.number}: ${e.message}`); }
  }
  const left = Object.values(permits).filter((p) => !p.details).length;
  log(`Addresses: ${found} of ${todo.length} found${left ? `, ${left} left for next time` : ""}`);
  status(`Done. ${Object.keys(permits).length} permits${left ? ` (run again later for ${left} more addresses)` : ""}. Click Download.`);
  $("gvz-dl").disabled = false;
  $("gvz-stop").remove();
})();
