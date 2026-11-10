"""Single-transfer suggestions ranked by a chosen measure."""
from __future__ import annotations

import pandas as pd

from .team import TeamState

# label -> (kind, value). kind "stat" uses a players_df column; "proj" sums the next N projected gameweeks.
METRICS = {
    "Current form": ("stat", "form_score"),
    "Total points": ("stat", "tp"),
    "4 week projections": ("proj", 4),
    "8 week projections": ("proj", 8),
    "12 week projections": ("proj", 12),
    "All season (remaining)": ("proj", 38),
    "Net transfers (this GW)": ("stat", "net_transfers"),
}

UNAVAILABLE = {"u", "i", "s", "n"}  # unavailable, injured, suspended, not in squad


def metric_values(metric: str, players: pd.DataFrame, proj: pd.DataFrame, next_gw: int) -> pd.Series:
    kind, val = METRICS[metric]
    if kind == "stat":
        if val == "net_transfers":
            return (players.transfers_in_event - players.transfers_out_event).astype(float)
        return players[val].astype(float)
    gws = range(next_gw, min(next_gw + val, 39))
    tot = proj[proj.gw.isin(gws)].groupby("fpl_id").pts.sum()
    return tot.reindex(players.index).fillna(0.0)


def suggestions(team: TeamState, players: pd.DataFrame, proj: pd.DataFrame, metric: str,
                top: int = 15, per_out: int = 3, positions: tuple[str, ...] | None = None) -> list[dict]:
    """Best affordable 1-for-1 swaps for the squad, sorted by the gain in `metric`."""
    values = metric_values(metric, players, proj, team.next_gw)
    clubs = players.loc[team.squad, "team"].value_counts()
    pool = players[~players.index.isin(team.squad) & ~players.status.isin(UNAVAILABLE)]
    rows = []
    for out in team.squad:
        o = players.loc[out]
        if positions and o.pos not in positions:
            continue
        money = team.bank + team.selling_price[out]
        cands = pool[(pool.element_type == o.element_type) & (pool.now_cost <= money)]
        ok = cands[(cands.team == o.team) | cands.team.map(lambda t: clubs.get(t, 0) < 3)]
        gains = (values.reindex(ok.index) - values.get(out, 0.0)).sort_values(ascending=False)
        for pid, g in gains.head(per_out).items():
            if g <= 0:
                break
            rows.append({"out_id": out, "in_id": int(pid), "gain": float(g),
                         "out_value": float(values.get(out, 0.0)), "in_value": float(values.get(pid, 0.0)),
                         "bank_after": money - int(players.at[pid, "now_cost"]),
                         "sell": team.selling_price[out]})
    rows.sort(key=lambda r: r["gain"], reverse=True)
    return rows[:top]
