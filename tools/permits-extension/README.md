# Permit collector, as a Chrome extension

The city's permit portal blocks cloud servers and program-controlled browsers, but it works in your everyday
Chrome. This extension runs `collect-permits.js` there for you: when you click **Run now**, it opens the portal in a
small background window, collects new permits and contractor names, publishes `docs/permits.json` to GitHub after
every batch, and closes the window when it's done. No pasting, no downloading, no uploading.

It never runs on its own: start it with **Run now** and stop it any time with **Stop** (or by closing Chrome). Every
finished batch is already on the site, and the next **Run now** picks up where it left off. A full re-read of every
permit takes a few hours; later runs only read what's new. You can keep using Chrome while it runs; just leave the
small portal window alone.

## Set up on a Mac (about 10 minutes, once)

### 1. Download the extension
Open Terminal and paste:
```
mkdir -p ~/grandview-permits-extension && cd ~/grandview-permits-extension
for f in manifest.json background.js popup.html popup.js icon.png; do curl -fsSLO https://raw.githubusercontent.com/uxandyphil/grandview-zoning/main/tools/permits-extension/$f; done
curl -fsSLO https://raw.githubusercontent.com/uxandyphil/grandview-zoning/main/collect-permits.js && echo "Downloaded."
```

### 2. Add it to Chrome
1. In Chrome, go to `chrome://extensions`.
2. Turn on **Developer mode** (top right).
3. Click **Load unpacked**, press **Command + Shift + G**, type `~/grandview-permits-extension`, press Enter, then click **Select**.
4. Click the puzzle-piece icon in Chrome's toolbar and pin **Grandview permits collector** (the house icon).

### 3. Add your GitHub key
Click the house icon. Under **GitHub key**, paste the key you made earlier (it starts with `github_pat_`) and click
**Save key**. It needs **Contents: Read and write** on `grandview-zoning` only. Lost it? On GitHub: your picture >
Settings > Developer settings > Personal access tokens > Fine-grained tokens > Generate new token.

### 4. Run it once
Click **Run now**. A small portal window opens in the corner and works on its own. It reads permits in batches (up to
80 when it has to open each permit's page) and uploads to the site after every batch, so the site fills in as it goes
(each upload shows up within a couple of minutes). **Recent activity** in the popup lists each batch and upload; "Done"
means the run finished.

If the portal starts refusing requests, the run stops by itself and a notification says so. Nothing is lost: try
**Run now** again later, for example the next day.

If a notification asks for one search, type `R-26-82` in the small window's search box and press Enter. It continues
by itself after that.

Run it again whenever you want the site's permits and contractors brought up to date.

## Updating
When the popup says a newer version is on GitHub, run the two download lines from step 1 again, then click the
reload arrow on the extension's card in `chrome://extensions`.

## Turning it off
Remove it from `chrome://extensions`, and delete the GitHub key on GitHub if you like.
