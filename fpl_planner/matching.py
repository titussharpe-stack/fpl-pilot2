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


POS_MAP = {"g": 1, "gk": 1, "gkp": 1, "goalkeeper": 1, "def": 2, "defender": 2, "d": 2,
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


def _name_variants(raw: str, look: dict[str, int]) -> tuple[list[str], int | None, int | None]:
    """A Scout cell may hold 'Haaland', or 'Haaland MCI FWD £14.5m' all in one. Split out the extras."""
    raw = re.sub(r"\(.*?\)", " ", str(raw))
    first_line = re.split(r"\s{2,}|\n|\|", raw.strip())[0]
    tokens = norm(raw).split()
    team = pos = None
    keep = []
    for tok in tokens:
        if team is None and tok in look and len(tok) >= 3 and tok.upper() == tok.upper():
            if tok in {norm(k) for k in look if len(k) == 3}:
                team = look[tok]
                continue
        if pos is None and tok in ("gk", "gkp", "def", "mid", "fwd"):
            pos = POS_MAP[tok]
            continue
        if re.fullmatch(r"\d+(\.\d+)?m?", tok):
            continue
        keep.append(tok)
    variants = [v for v in dict.fromkeys([norm(first_line), " ".join(keep), norm(raw)]) if v]
    return variants, team, pos


def match_players(rows: pd.DataFrame, players: pd.DataFrame) -> pd.Series:
    """rows needs 'name' and optional 'team' / 'pos'. Returns FPL id per row (NaN if unmatched)."""
    look = team_lookup(players)
    out = []
    for _, row in rows.iterrows():
        variants, tid2, pos2 = _name_variants(row["name"], look)
        tid = resolve_team(row.get("team", ""), look) if "team" in rows else None
        tid = tid if tid is not None else tid2
        pos = POS_MAP.get(norm(row.get("pos", ""))) if "pos" in rows else None
        pos = pos if pos is not None else pos2
        best = None
        for use_filters in (True, False):
            pool = players
            if use_filters and tid is not None:
                pool = pool[pool.team == tid]
            if use_filters and pos is not None:
                pool = pool[pool.element_type == pos]
            scored = sorted(((max(_name_score(q, r) for q in variants), pid) for pid, r in pool.iterrows()),
                            reverse=True)
            need = 0.72 if (use_filters and tid is not None) else 0.9
            if scored and scored[0][0] >= need:
                best = scored[0][1]
                break
        out.append(best)
    return pd.Series(out, index=rows.index, dtype="Int64")
