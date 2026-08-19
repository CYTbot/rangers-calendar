# Orton Rangers U11 Calendar

Publishes an auto-updating `.ics` fixture calendar for Orton Rangers U11,
subscribable from iPhone (or any calendar app that supports subscribed/
webcal calendars).

## How it works

1. `.github/workflows/update-calendar.yml` runs `generate_calendar.py`
   once a week (Friday evenings, UTC) and on manual trigger. The
   schedule is intentionally light to keep requests to the third-party
   fixtures proxy to a minimum -- use the Actions tab's "Run workflow"
   button any time you want a fresher check (e.g. Saturday morning
   before a match).
2. `generate_calendar.py` fetches all fixtures for the division from
   `faapi.jwhsolutions.co.uk` (a third-party JSON proxy for the FA
   Full-Time system), filters them down to Orton Rangers U11, and writes
   `public/orton-rangers-u11.ics`.
3. The `public/` folder is published to GitHub Pages, so the `.ics` file
   is reachable at a stable URL that iPhone (or any calendar app) can
   subscribe to.

Subscribe from `public/index.html`:
`https://cytbot.github.io/orton-rangers-calendar/`

Direct calendar URL (for the "add subscribed calendar" step):
`webcal://cytbot.github.io/orton-rangers-calendar/orton-rangers-u11.ics`

## Why a third-party proxy, not the FA site directly

`fulltime.thefa.com` blocks direct scraping (returns `403 Forbidden`,
including through the official `full-time-api` PyPI package). The
`faapi.jwhsolutions.co.uk` proxy currently returns the same fixture data
as clean JSON and is what this project relies on.

**This is an unofficial, undocumented service with no SLA** — it could
change or disappear without notice. The workflow is built to fail safe if
that happens:

- `generate_calendar.py` raises an error (and exits non-zero) if the
  fetch fails, or if it finds zero Orton Rangers U11 fixtures.
- The GitHub Actions `deploy` job only runs if `generate` succeeds, so a
  failed run leaves the **previously published calendar live** rather
  than publishing an empty or broken one.
- Check the **Actions** tab if fixtures stop updating — a red run means
  the proxy is down or the data shape changed, not that your calendar
  broke.

If the proxy disappears permanently, the fallback options are: find
another mirror/proxy, or switch to maintaining fixtures by hand in a data
file that `generate_calendar.py` reads instead of fetching.

## Updating for a new season

Each FA season/division has new IDs. In `generate_calendar.py`, update:

- `DIVISION_ID` and `SEASON_ID` — get these from the fixtures URL on
  `fulltime.thefa.com` for the relevant team/division (or by re-running
  `test_api.py`-style requests against the FA site with browser dev
  tools open, watching the network tab for the fixtures request).
- `TEAM_NAME` — must exactly match the team name as it appears in the FA
  fixture list (case-insensitive).

## First-time setup checklist

- [ ] Repo Settings → Pages → **Source: GitHub Actions** (required for
      the `deploy-pages` step to work)
- [ ] Run the workflow once manually (Actions tab → *Update Orton Rangers
      U11 Calendar* → Run workflow) to publish the first version
- [ ] Visit the Pages URL above and confirm the subscribe link works
