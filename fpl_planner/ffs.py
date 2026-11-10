"""Log in to Fantasy Football Scout and pull the season projections table.

Snapshots are saved once per gameweek to data/projections/gw<N>.csv in a long
format: fpl_id, name, team, pos, gw, pts. The raw page is kept in data/debug/
so the parser can be adjusted if Scout changes the page layout.
"""
from __future__ import annotations

import io
import json
import re
from datetime import datetime, timezone

import pandas as pd
import requests
from bs4 import BeautifulSoup

from . import fpl_api
from .config import DEBUG_DIR, PROJ_DIR, get_ffs_credentials
from .matching import match_players

MEMBERS = "https://members.fantasyfootballscout.co.uk"
PROJ_PATH = "/projections/season-projections/"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


class ScoutError(RuntimeError):
    pass


def _is_login_page(html: str) -> bool:
    return 'name="password"' in html and 'id="loginButton"' in html


def login(user: str, password: str) -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = UA
    s.get(f"{MEMBERS}/?url={PROJ_PATH}", timeout=30)
    r = s.post(f"{MEMBERS}/", data={"username": user, "password": password,
                                    "url": PROJ_PATH, "login": "Login"}, timeout=30)
    if _is_login_page(r.text):
        DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        (DEBUG_DIR / "last_page.html").write_text(r.text)
        raise ScoutError(f"Fantasy Football Scout login failed (status {r.status_code}, page {r.url}). "
                         "Check the email/password in Secrets – the email must match your Scout account.")
    return s


def fetch_html(session: requests.Session | None = None) -> str:
    if session is None:
        creds = get_ffs_credentials()
        if not creds:
            raise ScoutError("No Scout login stored. Run:  python -m fpl_planner login")
        session = login(*creds)
    r = session.get(f"{MEMBERS}{PROJ_PATH}", timeout=60)
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    (DEBUG_DIR / "last_page.html").write_text(r.text)
    r.raise_for_status()
    if _is_login_page(r.text):
        raise ScoutError("Scout sent us back to the login page – membership may lack projections access.")
    return r.text


# ---------- parsing ----------

GW_RE = re.compile(r"^(?:gw|gameweek|wk|week)?\s*(\d{1,2})$")


def _flatten(cols) -> list[str]:
    out = []
    for c in cols:
        if isinstance(c, tuple):
            parts = [str(x) for x in c if not str(x).startswith("Unnamed")]
            # prefer the most specific (last) label but keep GW numbers visible
            c = " ".join(dict.fromkeys(parts))
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
        label = label.split()[-1] if label.startswith(("pts", "points", "xp")) else label
        m = GW_RE.match(label) or re.match(r"^(?:gw|gameweek)\s*(\d{1,2})\b", label)
        if m and 1 <= int(m.group(1)) <= 38:
            found[c] = int(m.group(1))
    return found


def parse_tables(html: str) -> pd.DataFrame:
    """Return wide table: name, team, pos, price + one column per GW (int names)."""
    try:
        tables = pd.read_html(io.StringIO(html))
    except ValueError:
        tables = []
    best, best_score = None, 0
    for t in tables:
        cols = _flatten(t.columns)
        t.columns = cols
        score = len(t) * (1 + len(_gw_columns(cols))) * (1 if _find_col(cols, "player", "name") else 0)
        if score > best_score:
            best, best_score = t, score
    if best is None:
        raise ScoutError("No projections table found on the page (it may be loaded by JavaScript). "
                         "Saved the page in data/debug/ for inspection.")
    cols = list(best.columns)
    name_c = _find_col(cols, "player", "name")
    team_c = _find_col(cols, "team", "club")
    pos_c = _find_col(cols, "pos", "position")
    price_c = _find_col(cols, "price", "cost", "£")
    gws = _gw_columns(cols)
    out = pd.DataFrame({"name": best[name_c].astype(str)})
    if team_c:
        out["team"] = best[team_c].astype(str)
    if pos_c:
        out["pos"] = best[pos_c].astype(str)
    if price_c:
        out["price"] = pd.to_numeric(best[price_c].astype(str).str.replace("£", "").str.replace("m", ""), errors="coerce")
    if gws:
        for c, gw in gws.items():
            out[gw] = pd.to_numeric(best[c], errors="coerce")
    else:
        tot = _find_col(cols, "total", "pts", "points", "xp")
        if not tot:
            raise ScoutError(f"Couldn't find gameweek or points columns. Columns seen: {cols}")
        out["season_total"] = pd.to_numeric(best[tot], errors="coerce")
    out = out[out["name"].str.strip().ne("") & out["name"].ne("nan")]
    return out.reset_index(drop=True)


def to_long(wide: pd.DataFrame, players: pd.DataFrame, next_gw: int, last_gw: int = 38) -> tuple[pd.DataFrame, pd.DataFrame]:
    wide = wide.copy()
    wide["fpl_id"] = match_players(wide, players)
    unmatched = wide[wide.fpl_id.isna()]
    wide = wide.dropna(subset=["fpl_id"])
    gw_cols = [c for c in wide.columns if isinstance(c, int)]
    if gw_cols:
        long = wide.melt(id_vars=["fpl_id", "name"] + [c for c in ("team", "pos") if c in wide],
                         value_vars=gw_cols, var_name="gw", value_name="pts")
    else:  # only a season total: spread evenly over the remaining gameweeks
        remaining = last_gw - next_gw + 1
        rows = []
        for _, r in wide.iterrows():
            for gw in range(next_gw, last_gw + 1):
                rows.append({"fpl_id": r.fpl_id, "name": r["name"], "gw": gw, "pts": r.season_total / remaining})
        long = pd.DataFrame(rows)
    long = long.dropna(subset=["pts"])
    long = long[long.gw >= next_gw]
    long = long.groupby(["fpl_id", "gw"], as_index=False).agg(name=("name", "first"), pts=("pts", "sum"))
    long["fpl_id"] = long.fpl_id.astype(int)
    long["gw"] = long.gw.astype(int)
    return long, unmatched


# ---------- snapshots ----------

def snapshot_path(gw: int):
    return PROJ_DIR / f"gw{gw}.csv"


def scrape(force: bool = False) -> dict:
    boot = fpl_api.bootstrap()
    gw = fpl_api.next_gw(boot)
    path = snapshot_path(gw)
    if path.exists() and not force:
        return {"gw": gw, "path": str(path), "skipped": True}
    PROJ_DIR.mkdir(parents=True, exist_ok=True)
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    html = fetch_html()
    (DEBUG_DIR / f"gw{gw}.html").write_text(html)
    (DEBUG_DIR / "last_page.html").write_text(html)
    wide = parse_tables(html)
    long, unmatched = to_long(wide, fpl_api.players_df(boot), gw)
    long.to_csv(path, index=False)
    meta = {"gw": gw, "scraped_at": datetime.now(timezone.utc).isoformat(), "rows": len(wide),
            "matched_players": int(long.fpl_id.nunique()), "unmatched": unmatched["name"].tolist()[:200]}
    path.with_suffix(".json").write_text(json.dumps(meta, indent=2))
    return {**meta, "path": str(path), "skipped": False}


def latest_snapshot() -> tuple[pd.DataFrame, dict] | None:
    files = sorted(PROJ_DIR.glob("gw*.csv"), key=lambda p: int(re.findall(r"\d+", p.stem)[0]))
    if not files:
        return None
    f = files[-1]
    meta = json.loads(f.with_suffix(".json").read_text()) if f.with_suffix(".json").exists() else {}
    return pd.read_csv(f), meta
