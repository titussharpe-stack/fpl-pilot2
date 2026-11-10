"""Transfer suggestions for the next 4 gameweeks, one plan per factor.

Each factor gives every player a weekly score; the optimiser then picks the best
transfers for the next 4 gameweeks under FPL rules (budget, free transfers, hits,
3 per club, formation). Every plan is then scored on the same Scout projections
so the factors can be compared fairly.
"""
from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd

from .optimizer import Settings, solve
from .team import TeamState

TRANSFER_WEEKS = 4
UNAVAILABLE = {"u", "i", "s", "n"}  # unavailable, injured, suspended, not in squad

# label -> (kind, value). "stat": weekly score from an FPL statistic. "proj": Scout projections over N weeks.
FACTORS = {
    "Current form": ("stat", "form"),
    "Total points": ("stat", "tp"),
    "4 week projections": ("proj", 4),
    "8 week projections": ("proj", 8),
    "12 week projections": ("proj", 12),
    "All season": ("proj", 38),
    "Net transfers (this GW)": ("stat", "net"),
}


def _stat_scores(kind: str, players: pd.DataFrame, next_gw: int) -> pd.Series:
    """Turn a statistic into a points-like weekly score so hits and budget trade off sensibly."""
    if kind == "form":
        v = players.form_score.astype(float)
    elif kind == "tp":
        v = players.tp.astype(float) / max(next_gw - 1, 1)  # season points per gameweek so far
    else:  # net transfers: scale this week's net transfers onto 0..8
        net = (players.transfers_in_event - players.transfers_out_event).astype(float)
        top = max(np.percentile(net, 99.5), 1.0)
        v = (net.clip(lower=0) / top * 8).clip(upper=8)
    v = v.fillna(0.0).clip(lower=0)
    v[players.status.isin(UNAVAILABLE)] = 0.0
    return v


def factor_projections(factor: str, players: pd.DataFrame, proj: pd.DataFrame, next_gw: int):
    """(weekly scores long df, gameweeks the plan looks at) for a factor."""
    kind, val = FACTORS[factor]
    if kind == "proj":
        last = min(next_gw + val - 1, int(proj.gw.max()), 38)
        gws = list(range(next_gw, last + 1))
        return proj[proj.gw.isin(gws)], gws
    gws = list(range(next_gw, min(next_gw + TRANSFER_WEEKS, 39)))
    score = _stat_scores(val, players, next_gw)
    long = pd.DataFrame([(pid, g, sc) for pid, sc in score.items() for g in gws], columns=["fpl_id", "gw", "pts"])
    return long, gws


def factor_value(factor: str, players: pd.DataFrame, proj: pd.DataFrame, next_gw: int) -> pd.Series:
    """The raw number shown in the table for each player (form, TP, projected total, net transfers)."""
    kind, val = FACTORS[factor]
    if kind == "proj":
        gws = range(next_gw, min(next_gw + val, 39))
        return proj[proj.gw.isin(gws)].groupby("fpl_id").pts.sum().reindex(players.index).fillna(0.0)
    if val == "form":
        return players.form_score.astype(float)
    if val == "tp":
        return players.tp.astype(float)
    return (players.transfers_in_event - players.transfers_out_event).astype(float)


def plan_for_factor(factor: str, team: TeamState, players: pd.DataFrame, proj: pd.DataFrame,
                    settings: Settings) -> dict:
    scores, gws = factor_projections(factor, players, proj, team.next_gw)
    s = replace(settings, horizon=len(gws), transfer_weeks=TRANSFER_WEEKS, decay=1.0, ft_value=0.0,
                time_limit=min(settings.time_limit, 40))
    res = solve(players, scores, team.squad, team.selling_price, team.bank, team.free_transfers, gws, s)
    moves = []
    for p in res["plan"][:TRANSFER_WEEKS]:
        week = [{"gw": p["gw"], "out": o, "in": i, "hit": 0.0} for o, i in p["moves"]]
        # charge this week's hits to its weakest moves (by the factor itself)
        val = factor_value(factor, players, proj, team.next_gw)
        week.sort(key=lambda m: val.get(m["in"][0], 0) - val.get(m["out"][0], 0))
        for m in week[: p["hits"]]:
            m["hit"] = settings.hit_cost
        moves += sorted(week, key=lambda m: players.at[m["in"][0], "element_type"])
    return {"factor": factor, "moves": moves, "hits": sum(m["hit"] for m in moves),
            "plan_weeks": gws, "status": res["status"]}


def evaluate(moves: list[dict], proj: pd.DataFrame, next_gw: int) -> dict:
    """Score a set of moves on Scout projections: gain over next 4 / 8 weeks and rest of season."""
    wide = proj[proj.gw >= next_gw].pivot_table(index="fpl_id", columns="gw", values="pts", aggfunc="sum")

    def pts(pid, g):
        return float(wide.at[pid, g]) if pid in wide.index and g in wide.columns and pd.notna(wide.at[pid, g]) else 0.0

    out = {}
    for label, n in (("4 weeks", 4), ("8 weeks", 8), ("Season", 38)):
        weeks = range(next_gw, min(next_gw + n, 39))
        gain = sum(pts(m["in"][0], g) - pts(m["out"][0], g) for m in moves for g in weeks if g >= m["gw"])
        out[label] = gain - sum(m["hit"] for m in moves)
    return out


def all_factor_plans(team: TeamState, players: pd.DataFrame, proj: pd.DataFrame, settings: Settings,
                     progress=None) -> list[dict]:
    plans = []
    for k, f in enumerate(FACTORS):
        if progress:
            progress(k / len(FACTORS), f)
        plan = plan_for_factor(f, team, players, proj, settings)
        plan["eval"] = evaluate(plan["moves"], proj, team.next_gw)
        plans.append(plan)
    if progress:
        progress(1.0, "Done")
    return plans
