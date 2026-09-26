# Grandview Heights zoning watch

A public website listing every address on a Grandview Heights Board of
Zoning Appeals or Planning Commission agenda, with a map. A GitHub Action
checks the city's agenda archive every morning, extracts addresses from
new agenda PDFs, geocodes them, and republishes the site.

    scrape.py                     finds agendas, extracts cases, writes docs/cases.json
    docs/index.html               the website (reads cases.json)
    .github/workflows/update.yml  daily job
    state.json                    created on first run; tracks what's been processed

## Setup (about 10 minutes)

1. Create a **public** GitHub repo (GitHub Pages is free for public repos)
   and push these files, keeping the folder structure.
2. Settings > Pages: Source "Deploy from a branch", branch `main`, folder `/docs`.
   Your site will be at `https://<username>.github.io/<repo>/`.
3. Actions tab > update > Run workflow. The first run backfills the most
   recent 40 agendas per board (about 3 minutes, it pauses between requests).
4. Done. It updates daily at about 7am Eastern.

## Optional: email me new cases

Add repo secrets `SMTP_USER` (a Gmail address), `SMTP_PASS` (a Gmail app
password), and `EMAIL_TO`. Add a repo variable `SITE_URL` to include a link
to the site. Without these, no email is sent. The first run never emails.

## Test locally

    pip install -r requirements.txt
    python scrape.py --dry-run
    cd docs && python -m http.server   # open http://localhost:8000

To start over, delete `state.json` and reset `docs/cases.json` to
`{"updated": null, "cases": []}`.

## Known limits

- Addresses come from a regex, so an unusual format can be missed. Adjust
  `ADDRESS_RE` in `scrape.py` if you spot one.
- The script finds the agenda archives by name on the city's Archive Center
  page. If the Planning Commission never shows up, find its
  `Archive.aspx?AMID=` link on the city site and add it to `FALLBACK_ARCHIVES`.
- Only agendas are tracked, not building permits (those live in OpenGov,
  which blocks automated access).
