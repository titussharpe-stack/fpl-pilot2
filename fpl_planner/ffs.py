"""Pull projected points from Fantasy Football Scout's Rate My Team players page.

https://rate-my-team.fantasyfootballscout.co.uk/players/?...&first=A&last=B
shows each player's projected points over gameweeks A..B. We load it once per
remaining gameweek (first = last = that week) to get a per-gameweek projection
for every player, so the planner can look any number of weeks ahead.

Snapshots: data/projections/gw<N>.csv in long format (fpl_id, name, gw, pts),
taken once per gameweek. The last page received is kept in data/debug/ so the
parser can be adjusted if Scout changes the layout.
"""
from __future__ import annotations

import io
import json
import re
import time
from datetime import datetime, timezone
from urllib.parse import parse_qs, urljoin, urlparse

import pandas as pd
import requests
from bs4 import BeautifulSoup

from . import fpl_api
from .config import DEBUG_DIR, PROJ_DIR, get_ffs_credentials
from .matching import match_players

MEMBERS = "https://members.fantasyfootballscout.co.uk"
RMT = "https://rate-my-team.fantasyfootballscout.co.uk"
RMT_PLAYERS = RMT + "/players/?&team=0&position=0&maxval=0&minval=0&order=0&first={first}&last={last}"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
POLITE_DELAY = 0.7  # seconds between page loads


class ScoutError(RuntimeError):
    pass


def _is_login_page(html: str) -> bool:
    return 'name="password"' in html and ('id="loginButton"' in html or 'name="username"' in html)


def _debug(name: str, html: str) -> None:
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    (DEBUG_DIR / name).write_text(html)
    (DEBUG_DIR / "last_page.html").write_text(html)


def login(user: str, password: str, target: str | None = None) -> requests.Session:
    target = target or RMT_PLAYERS.format(first=1, last=1)
    s = requests.Session()
    s.headers["User-Agent"] = UA
    s.get(MEMBERS + "/", params={"url": target}, timeout=30)
    r = s.post(MEMBERS + "/", data={"username": user, "password": password, "url": target, "login": "Login"},
               timeout=30)
    if _is_login_page(r.text):
        _debug("login_failed.html", r.text)
        raise ScoutError(f"Fantasy Football Scout login failed (status {r.status_code}). "
                         "Check the email/password saved in Secrets.")
    return s


def _session() -> requests.Session:
    creds = get_ffs_credentials()
    if not creds:
        raise ScoutError("No Scout login stored (add FFS_USERNAME / FFS_PASSWORD to Secrets).")
    return login(*creds)


def fetch(session: requests.Session, url: str) -> str:
    r = session.get(url, timeout=60)
    if _is_login_page(r.text):
        # some sites need a second hop through the members redirect after login
        r = session.get(MEMBERS + "/", params={"url": url}, timeout=60)
    if _is_login_page(r.text):
        _debug("not_logged_in.html", r.text)
        raise ScoutError("Scout sent us back to the login page – the membership may not include Rate My Team.")
    r.raise_for_status()
    return r.text


# ---------- parsing ----------

GW_RE = re.compile(r"^(?:gw|gameweek|wk|week)?\s*(\d{1,2})$")
PTS_KEYS = ("pts", "points", "proj", "xp", "ep", "score", "total")


def _flatten(cols) -> list[str]:
    out = []
    for c in cols:
        if isinstance(c, tuple):
            c = " ".join(dict.fromkeys(str(x) for x in c if not str(x).startswith("Unnamed")))
        out.append(re.sub(r"\s+", " ", str(c)).strip())
    return out


def _find_col(cols: list[str], *keys: str) -> str | None:
    for k in keys:
        for c in cols:
            if c.lower() == k:
                return c
    for k in keys:
        for c in cols:
            if k in c.lower():
                return c
    return None


def _gw_columns(cols: list[str]) -> dict[str, int]:
    found = {}
    for c in cols:
        label = c.lower().strip()
        m = GW_RE.match(label) or re.match(r"^(?:gw|gameweek)\s*(\d{1,2})\b", label)
        if m and 1 <= int(m.group(1)) <= 38:
            found[c] = int(m.group(1))
    return found


def _num(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series.astype(str).str.replace(r"[£m,%]", "", regex=True).str.strip(), errors="coerce")


def _points_col(t: pd.DataFrame, cols: list[str], exclude: set[str]) -> str | None:
    for k in PTS_KEYS:  # exact header first, then contains
        for c in cols:
            if c not in exclude and c.lower() == k and _num(t[c]).notna().mean() > 0.8:
                return c
    for k in PTS_KEYS:
        for c in cols:
            if c not in exclude and k in c.lower() and _num(t[c]).notna().mean() > 0.8:
                return c
    return None


def parse_page(html: str) -> pd.DataFrame:
    """Return a table with name, team, pos, price and either `pts` or one column per GW (int)."""
    try:
        tables = pd.read_html(io.StringIO(html))
    except ValueError:
        tables = []
    best, best_score = None, 0
    for t in tables:
        cols = _flatten(t.columns)
        t.columns = cols
        if not _find_col(cols, "player", "name"):
            continue
        score = len(t) * (1 + len(_gw_columns(cols)))
        if score > best_score:
            best, best_score = t, score
    if best is None:
        raise ScoutError("No players table found on the Rate My Team page.")
    cols = list(best.columns)
    name_c = _find_col(cols, "player", "name")
    team_c = _find_col(cols, "team", "club")
    pos_c = _find_col(cols, "pos", "position")
    price_c = _find_col(cols, "price", "cost", "£", "value")
    out = pd.DataFrame({"name": best[name_c].astype(str).str.strip()})
    if team_c and team_c != name_c:
        out["team"] = best[team_c].astype(str)
    if pos_c and pos_c != name_c:
        out["pos"] = best[pos_c].astype(str)
    if price_c and price_c != name_c:
        out["price"] = _num(best[price_c])
    gws = _gw_columns(cols)
    if gws:
        for c, gw in gws.items():
            out[gw] = _num(best[c])
    else:
        pc = _points_col(best, cols, {name_c, team_c, pos_c, price_c})
        if not pc:
            raise ScoutError(f"Couldn't find a projected points column. Columns seen: {cols}")
        out["pts"] = _num(best[pc])
    out = out[out["name"].ne("") & out["name"].ne("nan")]
    return out.reset_index(drop=True)


def _page_links(html: str, base: str) -> list[str]:
    """Links to further pages of the same players list (if the list is paginated)."""
    soup = BeautifulSoup(html, "lxml")
    here = parse_qs(urlparse(base).query)
    links = []
    for a in soup.find_all("a", href=True):
        url = urljoin(base, a["href"])
        u = urlparse(url)
        if not u.path.rstrip("/").endswith("/players"):
            continue
        q = parse_qs(u.query)
        if q.get("first") != here.get("first") or q.get("last") != here.get("last"):
            continue
        if any(k in q for k in ("page", "start", "offset", "p", "pg")) and url not in links:
            links.append(url)
    return links[:30]


def fetch_range(session: requests.Session, first: int, last: int) -> pd.DataFrame:
    url = RMT_PLAYERS.format(first=first, last=last)
    html = fetch(session, url)
    _debug(f"rmt_{first}_{last}.html", html)
    frames = [parse_page(html)]
    for link in _page_links(html, url):
        time.sleep(POLITE_DELAY)
        frames.append(parse_page(fetch(session, link)))
    df = pd.concat(frames, ignore_index=True)
    return df.drop_duplicates(subset=[c for c in ("name", "team", "pos") if c in df])


def to_long(wide: pd.DataFrame, players: pd.DataFrame, gw: int | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Match names to FPL ids. `gw` labels a single-week `pts` column."""
    wide = wide.copy()
    wide["fpl_id"] = match_players(wide, players)
    unmatched = wide[wide.fpl_id.isna()]
    wide = wide.dropna(subset=["fpl_id"])
    gw_cols = [c for c in wide.columns if isinstance(c, int)]
    if gw_cols:
        long = wide.melt(id_vars=["fpl_id", "name"], value_vars=gw_cols, var_name="gw", value_name="pts")
    else:
        long = wide[["fpl_id", "name", "pts"]].assign(gw=gw)
    long = long.dropna(subset=["pts"])
    long = long.groupby(["fpl_id", "gw"], as_index=False).agg(name=("name", "first"), pts=("pts", "max"))
    long["fpl_id"] = long.fpl_id.astype(int)
    long["gw"] = long.gw.astype(int)
    return long, unmatched


# ---------- snapshots ----------

def snapshot_path(gw: int):
    return PROJ_DIR / f"gw{gw}.csv"


def scrape(force: bool = False, last_gw: int = 38, progress=None) -> dict:
    """Pull per-gameweek projections for every remaining gameweek (once per gameweek)."""
    boot = fpl_api.bootstrap()
    nxt = fpl_api.next_gw(boot)
    if nxt is None:
        raise ScoutError("The season has finished.")
    path = snapshot_path(nxt)
    if path.exists() and not force:
        return {"gw": nxt, "path": str(path), "skipped": True}
    PROJ_DIR.mkdir(parents=True, exist_ok=True)
    players = fpl_api.players_df(boot)
    s = _session()
    frames, unmatched = [], set()
    gws = list(range(nxt, last_gw + 1))
    for i, gw in enumerate(gws):
        if progress:
            progress(i / len(gws), f"Gameweek {gw}")
        wide = fetch_range(s, gw, gw)
        long, um = to_long(wide, players, gw)
        if long.empty:
            sample = wide.head(3).to_dict("records")
            raise ScoutError(f"Scout's table loaded ({len(wide)} rows) but no names matched FPL players. "
                             f"Columns: {list(wide.columns)}. First rows: {sample}")
        if not [c for c in wide.columns if isinstance(c, int)]:
            frames.append(long)
        else:  # page already gives per-GW columns: one load is enough
            frames = [long[long.gw >= nxt]]
            unmatched |= set(um["name"])
            break
        unmatched |= set(um["name"])
        time.sleep(POLITE_DELAY)
    data = pd.concat(frames, ignore_index=True)
    if data.empty:
        raise ScoutError("Scout pages loaded but no players could be matched to FPL.")
    data.to_csv(path, index=False)
    meta = {"gw": nxt, "source": "Rate My Team", "scraped_at": datetime.now(timezone.utc).isoformat(),
            "gameweeks": sorted(int(g) for g in data.gw.unique()),
            "matched_players": int(data.fpl_id.nunique()), "unmatched": sorted(map(str, unmatched))[:200]}
    path.with_suffix(".json").write_text(json.dumps(meta, indent=2))
    if progress:
        progress(1.0, "Done")
    return {**meta, "path": str(path), "skipped": False}


def latest_snapshot() -> tuple[pd.DataFrame, dict] | None:
    files = sorted(PROJ_DIR.glob("gw*.csv"), key=lambda p: int(re.findall(r"\d+", p.stem)[0]))
    if not files:
        return None
    f = files[-1]
    meta = json.loads(f.with_suffix(".json").read_text()) if f.with_suffix(".json").exists() else {}
    return pd.read_csv(f), meta
