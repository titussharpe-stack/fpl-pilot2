"""Match Fantasy Football Scout player rows to FPL player ids."""
from __future__ import annotations

import difflib
import re
import unicodedata

import pandas as pd

TEAM_ALIASES = {
    "manchester city": "man city", "manchester utd": "man utd", "manchester united": "man utd",
    "man united": "man utd", "tottenham hotspur": "spurs", "tottenham": "spurs",
    "newcastle united": "newcastle", "nottingham forest": "nott'm forest", "nottm forest": "nott'm forest",
    "notts forest": "nott'm forest", "wolverhampton": "wolves", "wolverhampton wanderers": "wolves",
    "brighton & hove albion": "brighton", "brighton and hove albion": "brighton",
    "west ham united": "west ham", "leeds united": "leeds", "afc bournemouth": "bournemouth",
    "sheffield utd": "sheffield utd", "sheffield united": "sheffield utd", "ipswich town": "ipswich",
    "leicester city": "leicester", "luton town": "luton", "crystal palace": "crystal palace",
}


def norm(s) -> str:
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9' ]", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def team_lookup(players: pd.DataFrame) -> dict[str, int]:
    look: dict[str, int] = {}
    for _, r in players.drop_duplicates("team").iterrows():
        look[norm(r.team_name)] = r.team
        look[norm(r.team_short)] = r.team
    for alias, canon in TEAM_ALIASES.items():
        if norm(canon) in look:
            look[norm(alias)] = look[norm(canon)]
    return look


def resolve_team(name, look: dict[str, int]) -> int | None:
    n = norm(name)
    if not n:
        return None
    if n in look:
        return look[n]
    best = difflib.get_close_matches(n, look.keys(), n=1, cutoff=0.75)
    return look[best[0]] if best else None


POS_MAP = {"gk": 1, "gkp": 1, "goalkeeper": 1, "def": 2, "defender": 2, "d": 2,
           "mid": 3, "midfielder": 3, "m": 3, "fwd": 4, "forward": 4, "fw": 4, "f": 4, "st": 4}


def _name_score(q: str, r) -> float:
    cands = {norm(r.web_name), norm(r.second_name), norm(f"{r.first_name} {r.second_name}"),
             norm(f"{str(r.first_name)[:1]} {r.second_name}")}
    best = 0.0
    for c in cands:
        if not c:
            continue
        if q == c:
            return 1.0
        best = max(best, difflib.SequenceMatcher(None, q, c).ratio())
        if q.split() and c.split() and q.split()[-1] == c.split()[-1]:
            best = max(best, 0.85)
    return best


def match_players(rows: pd.DataFrame, players: pd.DataFrame) -> pd.Series:
    """rows needs 'name' and optional 'team' / 'pos'. Returns FPL id per row (NaN if unmatched)."""
    look = team_lookup(players)
    out = []
    for _, row in rows.iterrows():
        q = norm(re.sub(r"\(.*?\)", "", str(row["name"])))
        pool = players
        tid = resolve_team(row.get("team", ""), look) if "team" in rows else None
        if tid is not None:
            pool = pool[pool.team == tid]
        pos = POS_MAP.get(norm(row.get("pos", ""))) if "pos" in rows else None
        if pos is not None:
            pool = pool[pool.element_type == pos]
        scored = sorted(((_name_score(q, r), pid) for pid, r in pool.iterrows()), reverse=True)
        ok = scored and scored[0][0] >= (0.72 if tid is not None else 0.92)
        out.append(scored[0][1] if ok else None)
    return pd.Series(out, index=rows.index, dtype="Int64")
