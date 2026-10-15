// Grandview permits collector (Chrome extension)
//
// Once a week (or when you click "Run now"), opens the city's permit portal in a small background window of your
// own Chrome, runs collect-permits.js there (the same script you'd paste by hand, which the portal accepts), and
// publishes the result to docs/permits.json on GitHub. Each step is driven by a timer that survives Chrome
// suspending this background script, so a run picks up where it was.

const REPO = "uxandyphil/grandview-zoning", BRANCH = "main", FILE = "docs/permits.json";
const PORTAL = "https://grandviewheightsoh.portal.opengov.com/search";
const WEEK_MIN = 7 * 24 * 60;
const MAX_BATCHES = 15;                 // each batch reads up to 200 permits, in a freshly loaded page
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

// ---------- schedule ----------
// Sundays at 9:07 PM, your time
function nextSunday() {
  const d = new Date();
  d.setHours(21, 7, 0, 0);
  d.setDate(d.getDate() + ((7 - d.getDay()) % 7));
  if (d <= new Date()) d.setDate(d.getDate() + 7);
  return d.getTime();
}
async function ensureSchedule() {
  const a = await chrome.alarms.get("weekly");
  if (!a) chrome.alarms.create("weekly", { when: nextSunday(), periodInMinutes: WEEK_MIN });
}
chrome.runtime.onInstalled.addListener(async () => {
  await ensureSchedule();
  if (await get("run")) chrome.alarms.create("tick", { periodInMinutes: 0.5 });   // updated mid-run: carry on
});
chrome.runtime.onStartup.addListener(async () => {
  await ensureSchedule();
  // if Chrome was closed at the scheduled time, catch up shortly after it opens
  const last = await get("lastSuccess");
  if (!last || Date.now() - last > (WEEK_MIN + 60) * 60_000) chrome.alarms.create("catchup", { delayInMinutes: 3 });
  if (await get("run")) chrome.alarms.create("tick", { periodInMinutes: 0.5 });
});
chrome.alarms.onAlarm.addListener((a) => {
  if (a.name === "weekly" || a.name === "catchup") startRun("scheduled");
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
  const win = await chrome.windows.create({ url: PORTAL, focused: false, width: 520, height: 460, left: 0, top: 0 });
  const tabId = win.tabs[0].id;
  try { await chrome.tabs.update(tabId, { autoDiscardable: false }); } catch {}
  await set({ run: { phase: "loading", windowId: win.id, tabId, batch: 1, at: Date.now(), searched: 0 } });
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
  const { status, result } = (await inPage(run.tabId, () => ({ status: window.GVZ_STATUS || "", result: window.GVZ_RESULT }))) || {};
  if (status !== run.status) await set({ run: { ...run, status } });
  if (!result) {
    if (/One step from you/.test(status || "")) {
      if (!run.searched) { await inPage(run.tabId, pageSearch); await set({ run: { ...run, status, searched: now } }); }
      else if (now - run.searched > 90_000 && !run.asked) {
        await chrome.windows.update(run.windowId, { focused: true });
        notify("Grandview permits needs one search", "In the small portal window, type R-26-82 in the search box and press Enter. It continues on its own after that.");
        await set({ run: { ...run, status, asked: true } });
      }
    }
    if (now - run.at > BATCH_LIMIT_MS) return finish(false, `Batch ${run.batch} took too long`);
    return;
  }
  if (result.error) return finish(false, `The portal refused the request (${result.error})`);
  await set({ lastJson: result.json });
  await note(`Batch ${run.batch}: ${result.total} permits, ${result.processed} read, contractors on ${result.withContractor}, ${result.left} left`);
  // upload after every batch, so the site gets progress even if the run stops partway. If Chrome interrupts this,
  // the next tick finds the same finished batch and uploads it again (an unchanged file is skipped).
  let published = run.published || 0;
  try {
    if (await publish(await get("token"), result.json, `${result.total} permits; ${result.left} still to read.`)) {
      published++;
      await note(`Uploaded to the site (${result.total} permits, ${result.left} still to read)`);
    }
  } catch (e) {
    await note(`Upload failed: ${e.message}${result.left ? "; it will try again after the next batch" : ""}`);
    if (!result.left || !result.processed || run.batch >= MAX_BATCHES) return finish(false, `Upload failed: ${e.message}`);
  }
  if (result.left && result.processed && run.batch < MAX_BATCHES) {
    await chrome.tabs.reload(run.tabId);   // a fresh page for each batch keeps memory low
    return set({ run: { ...run, phase: "loading", batch: run.batch + 1, at: now, status: "", searched: 0, asked: false, published } });
  }
  await finish(true, published ? `Done: ${result.total} permits on the site${result.left ? `, ${result.left} left for next time` : ""}`
    : "Done: no permit changes since the last upload");
}

async function finish(ok, message) {
  const run = await get("run");
  chrome.alarms.clear("tick");
  await chrome.storage.local.remove("run");
  if (run) { try { await chrome.windows.remove(run.windowId); } catch {} }
  await note(message);
  await set({ lastRun: { at: Date.now(), ok, message }, ...(ok ? { lastSuccess: Date.now() } : {}) });
  if (!ok && message !== "Stopped by you") notify("Grandview permits didn't finish", message);
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
async function gh(token, pathname, opts = {}) {
  const r = await fetch(`https://api.github.com${pathname}`, {
    ...opts,
    headers: { Authorization: `Bearer ${token}`, Accept: "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
      ...(opts.body ? { "Content-Type": "application/json" } : {}) },
  });
  if (!r.ok) throw new Error(`GitHub ${r.status}${r.status === 401 || r.status === 403 ? " (check the GitHub key)" : ""}`);
  return r.json();
}
// one commit to main replacing docs/permits.json; false when nothing changed
async function publish(token, json, summary) {
  for (let attempt = 1; attempt <= 3; attempt++) {
    const ref = await gh(token, `/repos/${REPO}/git/ref/heads/${BRANCH}`);
    const parent = ref.object.sha;
    const current = await fetch(`https://raw.githubusercontent.com/${REPO}/${parent}/${FILE}`).then((r) => (r.ok ? r.text() : ""));
    if (current && samePermits(current, json)) return false;
    const head = await gh(token, `/repos/${REPO}/git/commits/${parent}`);
    const blob = await gh(token, `/repos/${REPO}/git/blobs`, { method: "POST", body: JSON.stringify({ content: json, encoding: "utf-8" }) });
    const tree = await gh(token, `/repos/${REPO}/git/trees`, { method: "POST",
      body: JSON.stringify({ base_tree: head.tree.sha, tree: [{ path: FILE, mode: "100644", type: "blob", sha: blob.sha }] }) });
    const commit = await gh(token, `/repos/${REPO}/git/commits`, { method: "POST",
      body: JSON.stringify({ message: `Update permits from the Chrome collector\n\n${summary}`, tree: tree.sha, parents: [parent] }) });
    try {
      await gh(token, `/repos/${REPO}/git/refs/heads/${BRANCH}`, { method: "PATCH", body: JSON.stringify({ sha: commit.sha }) });
      return true;
    } catch (e) {
      if (attempt === 3) throw e;
      await new Promise((r) => setTimeout(r, 10_000));   // main moved (the nightly update); try again on top of it
    }
  }
}
