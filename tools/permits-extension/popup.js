const $ = (id) => document.getElementById(id);
const ago = (t) => { const m = Math.round((Date.now() - t) / 60000); return m < 2 ? "just now" : m < 120 ? `${m} minutes ago` : m < 2880 ? `${Math.round(m / 60)} hours ago` : `${Math.round(m / 1440)} days ago`; };

async function render() {
  const { run, lastRun, token, log } = await chrome.storage.local.get(["run", "lastRun", "token", "log"]);
  if (run) {
    const what = `Running: batch ${run.batch}${run.published ? ` · uploaded ${run.published} ${run.published === 1 ? "time" : "times"} so far` : ""}${run.status ? ` · ${run.status}` : ""}`;
    $("state").textContent = what;
  } else if (lastRun) {
    $("state").innerHTML = `<span class="${lastRun.ok ? "ok" : "bad"}">${lastRun.ok ? "Last run worked" : "Last run didn't finish"}</span> ${ago(lastRun.at)}<br><span class="muted"></span>`;
    $("state").querySelector(".muted").textContent = lastRun.message;
  } else $("state").textContent = token ? "Ready. It hasn't run yet." : "Add your GitHub key below, then click Run now.";
  $("run").hidden = !!run; $("stop").hidden = !run;
  $("keysum").textContent = token ? "GitHub key (saved)" : "GitHub key (needed)";
  if (!token) $("keybox").open = true;
  $("next").textContent = run ? "Uploads to the site after every batch. Click Stop any time; Run now later picks up where it left off."
    : "Runs only when you click Run now, and stops when it's done or you click Stop.";
  $("log").textContent = (log || []).slice().reverse().join("\n") || "Nothing yet.";
}

$("run").onclick = async () => { await chrome.runtime.sendMessage("run"); setTimeout(render, 500); };
$("stop").onclick = async () => { await chrome.runtime.sendMessage("stop"); render(); };
$("save").onclick = async () => {
  const t = $("token").value.trim();
  if (!/^(github_pat_|ghp_)\w+/.test(t)) { $("token").value = ""; $("token").placeholder = "That doesn't look like a GitHub key"; return; }
  await chrome.storage.local.set({ token: t });
  $("token").value = ""; $("keybox").open = false; render();
};

// Is there a newer collector on GitHub than the one in this folder?
(async () => {
  try {
    const [mine, latest] = await Promise.all([fetch(chrome.runtime.getURL("collect-permits.js")).then((r) => r.text()),
      fetch("https://raw.githubusercontent.com/uxandyphil/grandview-zoning/main/collect-permits.js", { cache: "no-store" }).then((r) => (r.ok ? r.text() : null))]);
    if (latest && latest.trim() !== mine.trim()) { $("update").hidden = false; $("update").textContent = "A newer version is on GitHub: run the update command from the setup guide, then click Reload on chrome://extensions."; }
  } catch {}
})();

render();
setInterval(render, 3000);
