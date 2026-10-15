# Permit collector, on a schedule

The city's permit portal blocks cloud servers, so permits can't be collected by the nightly GitHub job. This
small program does it from your own computer instead: on a schedule, it opens the portal in a hidden browser, runs
`collect-permits.js` (the newest copy from GitHub), and publishes the result to `docs/permits.json`. The site picks
it up right away. No clicking, no uploading.

Your computer needs to be on (asleep is fine on a Mac: it runs when the Mac wakes up). The first run reads every
permit and can take an hour or two; later runs only read what's new.

## Set up (about 15 minutes, once)

### 1. Install Node.js
Download the "LTS" version from https://nodejs.org and install it.

### 2. Get the program
**Mac:** open Terminal and paste:
```
mkdir -p ~/grandview-permits && cd ~/grandview-permits
for f in run.mjs package.json schedule-mac.sh; do curl -fsSLO https://raw.githubusercontent.com/uxandyphil/grandview-zoning/main/tools/permits-runner/$f; done
npm install && npx playwright install chromium
```
**Windows:** open PowerShell and paste:
```
mkdir $HOME\grandview-permits; cd $HOME\grandview-permits
foreach ($f in "run.mjs","package.json","schedule-windows.ps1") { Invoke-WebRequest "https://raw.githubusercontent.com/uxandyphil/grandview-zoning/main/tools/permits-runner/$f" -OutFile $f }
npm install; npx playwright install chromium
```
(Keep it in your home folder, not Documents or Desktop: on a Mac, scheduled programs can't open those folders.)

### 3. Make a GitHub key so it can publish
1. On GitHub: your picture > **Settings** > **Developer settings** > **Personal access tokens** > **Fine-grained tokens** > **Generate new token**.
2. Name it "Grandview permits", pick an expiration (1 year is fine), and under **Repository access** choose
   **Only select repositories** > `grandview-zoning`.
3. Under **Permissions** > **Repository permissions**, set **Contents** to **Read and write**. Nothing else.
4. Generate it and copy the key (it starts with `github_pat_`).
5. Save it next to the program. Mac: `echo 'GVZ_GITHUB_TOKEN=paste-the-key-here' > ~/grandview-permits/.env`.
   Windows: `Set-Content $HOME\grandview-permits\.env 'GVZ_GITHUB_TOKEN=paste-the-key-here'`.

The key can only change this one repository, and you can delete it on GitHub any time to switch this off.

### 4. Try it once
```
node run.mjs
```
It prints what it's doing (also saved in `runner.log`). Add `GVZ_SHOW=1` in front (Mac) to watch the browser.
When it says "Published docs/permits.json", the site will have the new permits within a minute or two.

### 5. Put it on a schedule (every Sunday, 9:07 PM)
- **Mac:** `bash schedule-mac.sh` (undo with `bash schedule-mac.sh remove`)
- **Windows:** `powershell -ExecutionPolicy Bypass -File schedule-windows.ps1` (undo with `... schedule-windows.ps1 remove`)

To change the day or time, edit the numbers in the schedule script and run it again.

## If something goes wrong
Look at `runner.log`. Common ones:
- **"GVZ_GITHUB_TOKEN is missing"**: step 3 wasn't saved in `.env` in this folder.
- **"401" or "403" when publishing**: the key expired or lacks Contents: Read and write. Make a new one (step 3).
- **"the portal refused the collector"**: run once with `GVZ_SHOW=1` to see what the portal shows.

Settings for `.env` (one per line): `GVZ_GITHUB_TOKEN` (required), `GVZ_SHOW=1` (show the browser),
`GVZ_DRY_RUN=1` (collect but don't publish; saves `permits.json` here), `GVZ_CHANNEL=chrome` (use your installed
Chrome instead of the one step 2 downloads).
