#!/usr/bin/env node
// Grandview permits runner
//
// Runs collect-permits.js on the city's OpenGov permit portal in a hidden browser on this computer (the portal
// blocks cloud servers, but not home internet), then publishes the result to docs/permits.json on GitHub.
// Meant to run on a schedule; see README.md in this folder.
//
// Settings go in a .env file next to this one (never committed):
//   GVZ_GITHUB_TOKEN=github_pat_...   required: a fine-grained token with "Contents: read and write" on the repo
//   GVZ_SHOW=1                         optional: show the browser window (for watching or troubleshooting)
//   GVZ_DRY_RUN=1                      optional: collect, but don't publish
import { chromium } from "playwright";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = "uxandyphil/grandview-zoning", BRANCH = "main", FILE = "docs/permits.json";
const PORTAL = process.env.GVZ_PORTAL || "https://grandviewheightsoh.portal.opengov.com/search";   // override only for testing
const MAX_BATCHES = 15;              // each batch reads up to 200 permits; a fresh page per batch keeps memory low
const BATCH_TIMEOUT = 75 * 60_000;   // give up on a batch after 75 minutes
const LOG_FILE = path.join(HERE, "runner.log");

function log(msg) {
  const line = `[${new Date().toLocaleString()}] ${msg}`;
  console.log(line);
  try { fs.appendFileSync(LOG_FILE, line + "\n"); } catch {}
}
function loadEnv() {
  const f = path.join(HERE, ".env");
  if (!fs.existsSync(f)) return;
  for (const line of fs.readFileSync(f, "utf8").split(/\r?\n/)) {
    const m = /^\s*([A-Z_]+)\s*=\s*(.*?)\s*$/.exec(line);
    if (m && !process.env[m[1]]) process.env[m[1]] = m[2].replace(/^["']|["']$/g, "");
  }
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// The collector: the newest copy on GitHub, so fixes reach this computer without reinstalling; else the local copy.
async function collectorSource() {
  if (process.env.GVZ_COLLECTOR_FILE) return fs.readFileSync(process.env.GVZ_COLLECTOR_FILE, "utf8");   // testing
  try {
    const r = await fetch(`https://raw.githubusercontent.com/${REPO}/${BRANCH}/collect-permits.js`, { cache: "no-store" });
    if (r.ok) return await r.text();
  } catch {}
  const local = path.join(HERE, "..", "..", "collect-permits.js");
  if (fs.existsSync(local)) return fs.readFileSync(local, "utf8");
  throw new Error("couldn't get collect-permits.js");
}

// The portal's first search sometimes needs to come from the page itself; type a permit number into its search box.
async function searchOnPage(page) {
  for (const sel of ['input[type="search"]', 'input[placeholder*="earch" i]', 'input[aria-label*="earch" i]', 'input[type="text"]']) {
    const box = page.locator(sel).first();
    if (await box.count() && await box.isVisible().catch(() => false)) {
      await box.fill("R-26-82");
      await box.press("Enter");
      return true;
    }
  }
  return false;
}

async function runBatch(ctx, src, n) {
  const page = await ctx.newPage();
  try {
    // A hidden browser calls itself "HeadlessChrome", which bot protection refuses; present as ordinary Chrome
    const ua = await page.evaluate(() => navigator.userAgent);
    if (/Headless/i.test(ua)) {
      const cdp = await ctx.newCDPSession(page);
      await cdp.send("Network.setUserAgentOverride", { userAgent: ua.replace(/HeadlessChrome/g, "Chrome") });
    }
    // note refused data requests (once each), to tell whether the portal itself or only the collector is blocked
    const refused = new Set();
    page.on("response", (r) => {
      const u = r.url();
      if (r.status() >= 400 && /viewpointcloud|\/api\/|search_results/i.test(u)) {
        const k = `${r.status()} ${u.split("?")[0].replace(/\d{3,}/g, "#")}`;
        if (!refused.has(k)) { refused.add(k); log(`  refused: ${k}`); }
      }
    });
    await page.goto(PORTAL, { waitUntil: "domcontentloaded", timeout: 120_000 });
    await page.waitForTimeout(5000);
    await page.evaluate(() => { window.GVZ_AUTO = true; });
    page.evaluate(src).catch((e) => log(`  collector stopped: ${e.message}`));
    const start = Date.now();
    let lastStatus = "", searched = false;
    while (Date.now() - start < BATCH_TIMEOUT) {
      await sleep(5000);
      const { status, result } = await page.evaluate(() => ({ status: window.GVZ_STATUS || "", result: window.GVZ_RESULT }));
      // log each new status, but progress counts ("Reading ...: 37 of 200") only every 50
      const progress = /: (\d+) of \d+$/.exec(status);
      if (status !== lastStatus && (!progress || +progress[1] % 50 === 0 || +progress[1] === 1)) log(`  ${status}`);
      lastStatus = status;
      if (/One step from you/.test(status) && !searched) { searched = await searchOnPage(page); log(searched ? "  searched the portal once" : "  couldn't find the portal's search box"); }
      if (result) return result;
    }
    log(`  batch ${n} timed out`);
    return null;
  } finally {
    await page.close().catch(() => {});
  }
}

// Same permits as what's published (ignoring map locations the nightly job adds, and timestamps)?
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
      "User-Agent": "grandview-permits-runner", ...(opts.body ? { "Content-Type": "application/json" } : {}) },
  });
  if (!r.ok) throw new Error(`${opts.method || "GET"} ${pathname}: ${r.status} ${(await r.text()).slice(0, 200)}`);
  return r.json();
}

// One commit to main that replaces docs/permits.json (works for files of any size, unlike the simple file API)
async function publish(token, json, summary) {
  for (let attempt = 1; attempt <= 3; attempt++) {
    const ref = await gh(token, `/repos/${REPO}/git/ref/heads/${BRANCH}`);
    const parent = ref.object.sha;
    const current = await fetch(`https://raw.githubusercontent.com/${REPO}/${parent}/${FILE}`).then((r) => (r.ok ? r.text() : ""));
    if (current && samePermits(current, json)) { log("No permit changes since the last upload; nothing to publish"); return; }
    const head = await gh(token, `/repos/${REPO}/git/commits/${parent}`);
    const blob = await gh(token, `/repos/${REPO}/git/blobs`, { method: "POST", body: JSON.stringify({ content: json, encoding: "utf-8" }) });
    const tree = await gh(token, `/repos/${REPO}/git/trees`, { method: "POST",
      body: JSON.stringify({ base_tree: head.tree.sha, tree: [{ path: FILE, mode: "100644", type: "blob", sha: blob.sha }] }) });
    const commit = await gh(token, `/repos/${REPO}/git/commits`, { method: "POST",
      body: JSON.stringify({ message: `Update permits from the scheduled collector\n\n${summary}`, tree: tree.sha, parents: [parent] }) });
    try {
      await gh(token, `/repos/${REPO}/git/refs/heads/${BRANCH}`, { method: "PATCH", body: JSON.stringify({ sha: commit.sha }) });
      log(`Published ${FILE} (commit ${commit.sha.slice(0, 7)})`);
      return;
    } catch (e) {
      if (attempt === 3) throw e;
      log("  the repo changed while publishing (probably the nightly update); trying again");
      await sleep(10_000);
    }
  }
}

// Your installed Chrome when there is one (it looks like an ordinary browser to the portal), else the downloaded one.
// Each keeps its own profile in this folder, so the collector remembers its progress between runs.
async function launch(visible) {
  const opts = {
    headless: !(visible || process.env.GVZ_SHOW), bypassCSP: true, viewport: { width: 1280, height: 900 },
    args: ["--disable-blink-features=AutomationControlled", ...(visible && !process.env.GVZ_SHOW ? ["--window-position=-2400,-2400"] : [])],
    ignoreDefaultArgs: ["--enable-automation"],
  };
  const choices = process.env.GVZ_BROWSER_PATH ? [{ executablePath: process.env.GVZ_BROWSER_PATH, name: "custom" }]
    : process.env.GVZ_CHANNEL ? [{ channel: process.env.GVZ_CHANNEL, name: process.env.GVZ_CHANNEL }]
    : [{ channel: "chrome", name: "chrome" }, { name: "chromium" }];
  for (const [i, c] of choices.entries()) {
    const { name, ...pick } = c;
    try {
      const ctx = await chromium.launchPersistentContext(path.join(HERE, `browser-profile-${name}`), { ...opts, ...pick });
      log(`Using ${name === "chrome" ? "your installed Chrome" : name === "chromium" ? "the downloaded browser" : name}${opts.headless ? " (hidden)" : ""}`);
      return ctx;
    } catch (e) {
      if (i === choices.length - 1) throw e;
    }
  }
}

async function main() {
  loadEnv();
  const token = process.env.GVZ_GITHUB_TOKEN, dry = !!process.env.GVZ_DRY_RUN;
  if (!token && !dry) throw new Error("GVZ_GITHUB_TOKEN is missing. Put it in tools/permits-runner/.env (see README.md).");
  log("Starting permit collection");
  const src = await collectorSource();
  let ctx = await launch(false);
  let result = null, retriedVisible = false;
  try {
    for (let n = 1; n <= MAX_BATCHES; n++) {
      log(`Batch ${n}`);
      const r = await runBatch(ctx, src, n);
      if (!r) break;
      if (r.error && !retriedVisible && !process.env.GVZ_SHOW) {
        // some bot protection refuses hidden browsers outright; try once more with a real window, placed off screen
        log("  refused in a hidden browser; trying again with a normal (off-screen) window");
        retriedVisible = true; await ctx.close(); ctx = await launch(true); n--; continue;
      }
      if (r.error) throw new Error(`the portal refused the collector: ${r.error}`);
      result = r;
      log(`  ${r.total} permits, ${r.processed} read this batch, contractors on ${r.withContractor}, ${r.left} left`);
      if (!r.left || !r.processed) break;
    }
  } finally {
    await ctx.close();
  }
  if (!result) throw new Error("the collector didn't finish a batch");
  const summary = `${result.total} permits; ${result.left} still to read.`;
  if (dry) { fs.writeFileSync(path.join(HERE, "permits.json"), result.json); log(`Dry run: saved permits.json here. ${summary}`); return; }
  await publish(token, result.json, summary);
  log(`Done. ${summary}`);
}

main().catch((e) => { log(`FAILED: ${e.message}`); process.exitCode = 1; });
