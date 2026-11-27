// Grandview permits collector (Chrome extension)
//
// When you click "Run now", opens each city's permit portal in turn in a small background window of your own Chrome,
// runs collect-permits.js there (the same script you'd paste by hand, which the portals accept), and publishes the
// result to GitHub after each batch. It runs only when started, and stops when it's done or you click "Stop". Each
// step is driven by a timer that survives Chrome suspending this background script.

const REPO = "uxandyphil/grandview-zoning", BRANCH = "main";
// Each city's portal and the file its permits go to. A run alternates batches between the cities that still have
// permits to read, so both fill in from the start; if one city's portal refuses, the run carries on with the other.
const CITIES = [
  { name: "Grandview", portal: "https://grandviewheightsoh.portal.opengov.com/search", file: "docs/permits.json", commit: "permits",
    search: "R-26-82" },
  { name: "Upper Arlington", portal: "https://upperarlingtonoh.portal.opengov.com/search", file: "docs/permits-ua.json",
    commit: "Upper Arlington permits", search: "a street name" },
];
const MAX_BATCHES = 80;                 // each batch reads up to 200 permits (80 when whole pages are read), in a freshly loaded page
const BATCH_LIMIT_MS = 75 * 60_000;     // give up on a batch after 75 minutes

const get = async (k) => (await chrome.storage.local.get(k))[k];
const set = (o) => chrome.storage.local.set(o);
async function note(line) {
  const log = (await get("log")) || [];
  log.push(`${new Date().toLocaleString()}  ${line}`);
  await set({ log: log.slice(-60) });
}
function notify(title, message) {
  chrome.notifications.create({ type: "basic", iconUrl: "icon.png", title, message });
}

// ---------- starting and stopping ----------
// No schedule: it runs only when you click "Run now". (Older versions also ran every Sunday; that alarm is removed.)
async function clearOldSchedule() {
  await chrome.alarms.clear("weekly");
  await chrome.alarms.clear("catchup");
}
chrome.runtime.onInstalled.addListener(async () => {
  await clearOldSchedule();
  if (await get("run")) chrome.alarms.create("tick", { periodInMinutes: 0.5 });   // reloaded mid-run: carry on
});
chrome.runtime.onStartup.addListener(async () => {
  await clearOldSchedule();
  // Chrome was closed during a run: don't restart it on its own. Every finished batch is already on the site, and
  // "Run now" picks up where it left off.
  if (await get("run")) await finish(false, "Chrome was closed during the run. Click Run now to pick up where it left off.");
});
chrome.alarms.onAlarm.addListener((a) => {
  if (a.name === "tick") tick();
});
chrome.runtime.onMessage.addListener((msg, _s, reply) => {
  if (msg === "run") startRun("by you").then(() => reply(true));
  if (msg === "stop") finish(false, "Stopped by you").then(() => reply(true));
  return true;
});

// ---------- a run ----------
async function startRun(why) {
  if (await get("run")) return;
  if (!(await get("token"))) { await note("Skipped: no GitHub key saved yet (open the extension to add it)"); return; }
  const win = await chrome.windows.create({ url: CITIES[0].portal, focused: false, width: 520, height: 460, left: 0, top: 0 });
  const tabId = win.tabs[0].id;
  try { await chrome.tabs.update(tabId, { autoDiscardable: false }); } catch {}
  await set({ run: { phase: "loading", windowId: win.id, tabId, batch: 1, at: Date.now(), searched: 0, city: 0,
    over: CITIES.map(() => false), results: CITIES.map(() => null) } });
  await note(`Started (${why})`);
  chrome.alarms.create("tick", { periodInMinutes: 0.5 });
}

async function inPage(tabId, func, args = []) {
  const [r] = await chrome.scripting.executeScript({ target: { tabId }, world: "MAIN", func, args });
  return r && r.result;
}
// best effort at the portal's one-time "search R-26-82" step
function pageSearch() {
  const inputs = [...document.querySelectorAll("input")].filter((i) => i.offsetParent && !/hidden|checkbox|radio/.test(i.type));
  const box = inputs.find((i) => /search/i.test(`${i.type} ${i.placeholder} ${i.getAttribute("aria-label")}`)) || inputs[0];
  if (!box) return false;
  box.focus();
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(box, "R-26-82");
  box.dispatchEvent(new Event("input", { bubbles: true }));
  box.dispatchEvent(new Event("change", { bubbles: true }));
  for (const t of ["keydown", "keypress", "keyup"]) box.dispatchEvent(new KeyboardEvent(t, { key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true }));
  if (box.form && box.form.requestSubmit) box.form.requestSubmit();
  return true;
}

let ticking = false;
async function tick() {
  if (ticking) return;
  ticking = true;
  try { await step(); } catch (e) { await finish(false, `Error: ${e.message}`); } finally { ticking = false; }
}
async function step() {
  const run = await get("run");
  if (!run) { chrome.alarms.clear("tick"); return; }
  let tab;
  try { tab = await chrome.tabs.get(run.tabId); } catch { return finish(false, "The collector's window was closed"); }
  const now = Date.now();
  if (run.phase === "loading") {
    if (tab.status !== "complete" || now - run.at < 8000) return;
    await inPage(run.tabId, () => { window.GVZ_AUTO = true; window.GVZ_RESULT = null; });
    // started, not awaited: Chrome would otherwise wait for the whole collector run (up to an hour) to finish
    chrome.scripting.executeScript({ target: { tabId: run.tabId }, world: "MAIN", files: ["collect-permits.js"] })
      .catch((e) => note(`Couldn't start the collector: ${e.message}`));
    return set({ run: { ...run, phase: "running", at: now } });
  }
  if (run.phase !== "running") return;
  const ci = run.city || 0, city = CITIES[ci];
  let { status, result } = (await inPage(run.tabId, () => ({ status: window.GVZ_STATUS || "", result: window.GVZ_RESULT }))) || {};
  if (status !== run.status) await set({ run: { ...run, status } });
  if (!result) {
    if (/One step from you/.test(status || "")) {
      if (!run.searched) { await inPage(run.tabId, pageSearch); await set({ run: { ...run, status, searched: now } }); }
      else if (now - run.searched > 90_000 && !run.asked) {
        await chrome.windows.update(run.windowId, { focused: true });
        notify("The permit collector needs one search", `In the small ${city.name} portal window, type ${city.search} in the search box and press Enter. It continues on its own after that.`);
        await set({ run: { ...run, status, asked: true } });
      }
    }
    if (now - run.at <= BATCH_LIMIT_MS) return;
    result = { error: "a batch took too long", timedOut: true };   // give up on this city for this run, carry on with the other
  }
  const over = run.over || CITIES.map(() => false), results = run.results || CITIES.map(() => null);
  let published = run.published || 0;
  if (result.error) {
    over[ci] = true;
    results[ci] = { error: result.timedOut ? result.error : `the portal refused the request (${result.error})` };
    await note(`${city.name}: ${results[ci].error}`);
  } else {
    await set({ lastJson: result.json });
    await note(`${city.name} batch: ${result.total} permits, ${result.processed} read, contractors on ${result.withContractor}, ${result.left} left`);
    // upload after every batch, so the site gets progress even if the run stops partway. If Chrome interrupts this,
    // the next tick finds the same finished batch and uploads it again (an unchanged file is skipped).
    results[ci] = { total: result.total, left: result.left };
    try {
      if (await publish(await get("token"), result.json, `${result.total} permits; ${result.left} still to read.`, city)) {
        published++;
        await note(`Uploaded ${city.name} to the site (${result.total} permits, ${result.left} still to read)`);
      }
    } catch (e) {
      await note(`${city.name} upload failed: ${e.message}${result.left ? "; it will try again after the next batch" : ""}`);
      results[ci].error = `the upload failed (${e.message})`;
    }
    if (!result.left || !result.processed) over[ci] = true;   // nothing left, or a batch that read nothing
  }
  // next: the other city if it still has permits to read, else this one again
  const next = [(ci + 1) % CITIES.length, ci].find((i) => !over[i]);
  if (next !== undefined && run.batch < MAX_BATCHES) {
    if (next === ci) await chrome.tabs.reload(run.tabId);   // a fresh page for each batch keeps memory low
    else await chrome.tabs.update(run.tabId, { url: CITIES[next].portal });
    return set({ run: { ...run, phase: "loading", city: next, batch: run.batch + 1, at: now, status: "", searched: 0, asked: false,
      published, over, results } });
  }
  const summary = CITIES.map((c, i) => results[i] && (results[i].error ? `${c.name} stopped: ${results[i].error}`
    : `${c.name}: ${results[i].total.toLocaleString()} permits on the site${results[i].left ? `, ${results[i].left.toLocaleString()} left for next time` : ""}`))
    .filter(Boolean).join(". ");
  const ok = !results.some((r) => r && r.error);
  await finish(ok, `${!ok ? "" : published ? "Done. " : "Done: no permit changes since the last upload. "}${summary}`);
}

async function finish(ok, message) {
  const run = await get("run");
  chrome.alarms.clear("tick");
  await chrome.storage.local.remove("run");
  if (run) { try { await chrome.windows.remove(run.windowId); } catch {} }
  await note(message);
  await set({ lastRun: { at: Date.now(), ok, message } });
  if (!ok && message !== "Stopped by you") notify("The permit collector didn't finish", message);
}

// ---------- publishing to GitHub ----------
function samePermits(a, b) {
  const norm = (txt) => {
    try {
      return JSON.stringify((JSON.parse(txt).permits || []).map(({ lat, lon, ...p }) => p).sort((x, y) => String(x.number).localeCompare(String(y.number))),
        (k, v) => (v && typeof v === "object" && !Array.isArray(v) ? Object.fromEntries(Object.entries(v).sort()) : v));
    } catch { return null; }
  };
  const na = norm(a);
  return na !== null && na === norm(b);
}
// The site's nightly job adds map locations to docs/permits.json; carry them into the upload so it doesn't take the
// permits' pins off the map until the next nightly run
function withMapLocations(json, current) {
  try {
    const cur = new Map((JSON.parse(current).permits || []).filter((p) => p.lat != null).map((p) => [p.number, p]));
    const data = JSON.parse(json);
    for (const p of data.permits || []) {
      const c = cur.get(p.number);
      if (c && p.lat == null) { p.lat = c.lat; p.lon = c.lon; }
    }
    return JSON.stringify(data, null, 2);
  } catch { return json; }
}
async function gh(token, pathname, opts = {}) {
  const r = await fetch(`https://api.github.com${pathname}`, {
    ...opts,
    headers: { Authorization: `Bearer ${token}`, Accept: "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
      ...(opts.body ? { "Content-Type": "application/json" } : {}) },
  });
  if (!r.ok) throw new Error(`GitHub ${r.status}${r.status === 401 || r.status === 403 ? " (check the GitHub key)" : ""}`);
  return r.json();
}
// one commit to main replacing the city's file; false when nothing changed
async function publish(token, json, summary, city) {
  const FILE = city.file;
  for (let attempt = 1; attempt <= 3; attempt++) {
    const ref = await gh(token, `/repos/${REPO}/git/ref/heads/${BRANCH}`);
    const parent = ref.object.sha;
    const current = await fetch(`https://raw.githubusercontent.com/${REPO}/${parent}/${FILE}`).then((r) => (r.ok ? r.text() : ""));
    if (current && samePermits(current, json)) return false;
    json = withMapLocations(json, current);
    const head = await gh(token, `/repos/${REPO}/git/commits/${parent}`);
    const blob = await gh(token, `/repos/${REPO}/git/blobs`, { method: "POST", body: JSON.stringify({ content: json, encoding: "utf-8" }) });
    const tree = await gh(token, `/repos/${REPO}/git/trees`, { method: "POST",
      body: JSON.stringify({ base_tree: head.tree.sha, tree: [{ path: FILE, mode: "100644", type: "blob", sha: blob.sha }] }) });
    const commit = await gh(token, `/repos/${REPO}/git/commits`, { method: "POST",
      body: JSON.stringify({ message: `Update ${city.commit} from the Chrome collector\n\n${summary}`, tree: tree.sha, parents: [parent] }) });
    try {
      await gh(token, `/repos/${REPO}/git/refs/heads/${BRANCH}`, { method: "PATCH", body: JSON.stringify({ sha: commit.sha }) });
      return true;
    } catch (e) {
      if (attempt === 3) throw e;
      await new Promise((r) => setTimeout(r, 10_000));   // main moved (the nightly update); try again on top of it
    }
  }
}
