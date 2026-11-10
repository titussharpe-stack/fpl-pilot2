# FPL Transfer Planner

Enter an FPL team ID → see bank, squad selling value, total budget and free
transfers → get the best transfer plan for however many gameweeks ahead you
choose (1 week up to the rest of the season), based on Fantasy Football Scout's
Rate My Team projected points.

Projections come from
`https://rate-my-team.fantasyfootballscout.co.uk/players/?...&first=A&last=B`.
The app loads that page once per remaining gameweek (first = last = that week)
to get a per-gameweek projection for every player.

## Setup
```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## 1. Store your Scout login (once)
```bash
python -m fpl_planner login
```
Asks for email + password (password is hidden), checks it works, then saves it
in your computer's password vault (macOS Keychain / Windows Credential Manager /
Linux Secret Service). It is never written to a file or the code.
Remove it with `python -m fpl_planner logout`.

On a server without a vault, set `FFS_USERNAME` and `FFS_PASSWORD` environment
variables instead.

## 2. Pull projections (once per gameweek)
```bash
python -m fpl_planner scrape            # now, if this GW isn't saved yet
python -m fpl_planner scrape --force    # re-pull
python -m fpl_planner schedule          # leave running: scrapes 30h before each deadline
python -m fpl_planner plan 2804900 --horizon 10   # plan 10 weeks ahead
```
Or with cron (checks hourly, scrapes once per GW):
`0 * * * * cd /path/to/fpl-planner && .venv/bin/python -m fpl_planner scrape --if-due`

Snapshots go to `data/projections/gw<N>.csv`; the raw page is kept in `data/debug/`.

## 3. Review a team
```bash
streamlit run app.py                    # web page
python -m fpl_planner plan 2804900      # terminal
```

## How it works
- **Budget**: bank + selling price of each player (FPL rule: you keep half of any
  price rise), worked out from your transfer history. Transfers already made for
  the upcoming GW are included.
- **Free transfers**: rebuilt from history (+1 per GW, max 5, kept on Wildcard/Free Hit).
  One-off FPL top-ups aren't visible in the public API, so you can override in the app.
- **Optimiser**: a mixed-integer model over 4 GWs choosing squad, starting XI and
  captain each week, with valid formations, max 3 per club, budget, rolling free
  transfers and -4 hits. Compared against making no transfers.
- Chips (Wildcard, Free Hit, Bench Boost, Triple Captain) aren't planned yet.

## Tests
```bash
pytest -q
```

## Hosting on Streamlit Community Cloud (no install)
1. Push this repo to GitHub.
2. At share.streamlit.io: Create app → pick the repo → main file `app.py` → Deploy.
3. App menu → Settings → Secrets, paste:
   ```
   FFS_USERNAME = "you@example.com"
   FFS_PASSWORD = "your-password"
   ```
   Streamlit stores these encrypted and exposes them to the app as environment variables.
The app pulls Scout projections automatically the first time it's opened each gameweek.
