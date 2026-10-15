# Permit collector, as a Chrome extension

The city's permit portal blocks cloud servers and program-controlled browsers, but it works in your everyday
Chrome. This extension runs `collect-permits.js` there for you: once a week it opens the portal in a small
background window, collects new permits and contractor names, publishes `docs/permits.json` to GitHub, and closes
the window. No pasting, no downloading, no uploading.

Chrome needs to be open at the scheduled time. If it isn't, the extension catches up a few minutes after Chrome
next opens. The first run reads every permit and can take an hour or two; later runs only read what's new. You can
keep using Chrome while it runs; just leave the small portal window alone.

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
Click **Run now**. A small portal window opens in the corner and works on its own. It reads permits in batches of
up to 200 and uploads to the site after every batch, so the site fills in as it goes (each upload shows up within a
couple of minutes). **Recent activity** in the popup lists each batch and upload; "Done" means the run finished.

If a notification asks for one search, type `R-26-82` in the small window's search box and press Enter. It continues
by itself after that.

That's it: it now runs every week on its own.

## Updating
When the popup says a newer version is on GitHub, run the two download lines from step 1 again, then click the
reload arrow on the extension's card in `chrome://extensions`.

## Turning it off
Remove it from `chrome://extensions`, and delete the GitHub key on GitHub if you like.
