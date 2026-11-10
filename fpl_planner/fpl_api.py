"""Thin client for the public Fantasy Premier League API (no login needed)."""
from __future__ import annotations

import time
from functools import lru_cache

import pandas as pd
import requests

BASE = "https://fantasy.premierleague.com/api"
_session = requests.Session()
_session.headers["User-Agent"] = "Mozilla/5.0 (fpl-planner)"


def _get(path: str):
    for attempt in range(3):
        r = _session.get(f"{BASE}/{path}", timeout=30)
        if r.status_code == 200:
            return r.json()
        if r.status_code == 404:
            raise ValueError(f"FPL API: not found ({path})")
        time.sleep(1 + attempt)
    r.raise_for_status()


@lru_cache(maxsize=1)
def _bootstrap_cached(_bucket: int):
    return _get("bootstrap-static/")


def bootstrap() -> dict:
    # cache for 10 minutes
    return _bootstrap_cached(int(time.time() // 600))


def entry(team_id: int) -> dict:
    return _get(f"entry/{team_id}/")


def history(team_id: int) -> dict:
    return _get(f"entry/{team_id}/history/")


def picks(team_id: int, gw: int) -> dict:
    return _get(f"entry/{team_id}/event/{gw}/picks/")


def transfers(team_id: int) -> list[dict]:
    return _get(f"entry/{team_id}/transfers/")


def players_df(boot: dict | None = None) -> pd.DataFrame:
    boot = boot or bootstrap()
    teams = {t["id"]: t for t in boot["teams"]}
    df = pd.DataFrame(boot["elements"])
    df = df[["id", "web_name", "first_name", "second_name", "team", "element_type",
             "now_cost", "cost_change_start", "status", "chance_of_playing_next_round", "news",
             "selected_by_percent", "total_points", "form", "points_per_game", "minutes", "goals_scored",
             "assists", "clean_sheets", "bonus", "expected_goals", "expected_assists", "ict_index",
             "transfers_in_event", "transfers_out_event"]].copy()
    num = ["selected_by_percent", "form", "points_per_game", "expected_goals", "expected_assists", "ict_index"]
    df[num] = df[num].apply(pd.to_numeric, errors="coerce")
    # short names used across the app: TSB = selected by %, TP = total points, Form = FPL form
    df["tsb"] = df["selected_by_percent"]
    df["tp"] = df["total_points"]
    df["form_score"] = df["form"]
    df["team_name"] = df["team"].map(lambda t: teams[t]["name"])
    df["team_short"] = df["team"].map(lambda t: teams[t]["short_name"])
    df["pos"] = df["element_type"].map({1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"})
    return df.set_index("id")


def events(boot: dict | None = None) -> pd.DataFrame:
    boot = boot or bootstrap()
    ev = pd.DataFrame(boot["events"])[["id", "deadline_time", "finished", "is_current", "is_next"]]
    ev["deadline_time"] = pd.to_datetime(ev["deadline_time"], utc=True)
    return ev.set_index("id")


def next_gw(boot: dict | None = None) -> int | None:
    for e in (boot or bootstrap())["events"]:
        if e["is_next"]:
            return e["id"]
    return None
