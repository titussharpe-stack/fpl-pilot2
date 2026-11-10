"""Multi-gameweek transfer planner (mixed-integer programme).

Maximises projected points over the horizon:
  starting XI points + captain bonus + small weight on bench − 4 per extra transfer
subject to FPL rules: 2/5/5/3 squad, max 3 per club, valid formation, budget
(selling prices for players you own, current price for new ones), free transfers
that roll over (max 5).
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import pulp

SQUAD = {1: 2, 2: 5, 3: 5, 4: 3}
XI_MIN = {1: 1, 2: 3, 3: 2, 4: 1}
XI_MAX = {1: 1, 2: 5, 3: 5, 4: 3}


@dataclass
class Settings:
    horizon: int = 4
    hit_cost: float = 4.0
    max_hits_per_gw: int = 2
    max_transfers_per_gw: int = 5
    bench_weight: float = 0.1
    decay: float = 1.0            # weight of week k is decay**k
    ft_value: float = 0.0         # value of each free transfer left at the end
    locked: tuple = ()            # must keep / must have
    banned: tuple = ()            # never buy
    pool_per_pos: int | None = None  # candidates per position (auto: smaller for long horizons)
    time_limit: int = 60          # seconds; returns best plan found if it runs out


def _pool(players, proj, squad, s: Settings) -> list[int]:
    tot = proj.sum(axis=1)
    n_top = s.pool_per_pos or max(15, 60 - 3 * (proj.shape[1] - 1))
    keep = set(squad) | set(s.locked)
    df = players.loc[players.index.intersection(proj.index)].copy()
    df["tot"] = tot
    df = df[df.status.ne("u") & ~df.index.isin(s.banned)]
    for pos in SQUAD:
        d = df[df.element_type == pos]
        keep |= set(d.nlargest(n_top, "tot").index)
        keep |= set(d.nsmallest(6, "now_cost").index)          # cheap bench fodder
        keep |= set((d.tot / d.now_cost).nlargest(15).index)   # value picks
    return sorted(p for p in keep if p in players.index)


def solve(players: pd.DataFrame, projections: pd.DataFrame, squad: list[int],
          selling: dict[int, int], bank: int, free_transfers: int, gws: list[int],
          s: Settings = Settings(), allow_transfers: bool = True) -> dict:
    """projections: long df with fpl_id, gw, pts."""
    proj = projections[projections.gw.isin(gws)].pivot_table(index="fpl_id", columns="gw", values="pts", aggfunc="sum")
    proj = proj.reindex(columns=gws).fillna(0.0)
    for p in squad:
        if p not in proj.index:
            proj.loc[p] = 0.0
    P = _pool(players, proj, squad, s)
    proj = proj.reindex(P).fillna(0.0)
    T = list(range(len(gws)))
    pos = players.loc[P, "element_type"].to_dict()
    club = players.loc[P, "team"].to_dict()
    buy = players.loc[P, "now_cost"].astype(int).to_dict()
    sell = {p: selling.get(p, buy[p]) for p in P}
    owned = {p: int(p in squad) for p in P}
    pts = {(p, t): float(proj.at[p, gws[t]]) for p in P for t in T}
    w = {t: s.decay ** t for t in T}

    m = pulp.LpProblem("fpl", pulp.LpMaximize)
    B = pulp.LpVariable.dicts
    x = B("squad", (P, T), cat="Binary")
    l = B("xi", (P, T), cat="Binary")
    c = B("cap", (P, T), cat="Binary")
    bi = B("in", (P, T), cat="Binary")
    so = B("out", (P, T), cat="Binary")
    bank_v = B("bank", T, lowBound=0)
    ft = B("ft", range(len(T) + 1), lowBound=0, upBound=5, cat="Integer")
    hits = B("hits", T, lowBound=0, upBound=s.max_hits_per_gw, cat="Integer")

    m += pulp.lpSum(w[t] * (pulp.lpSum(pts[p, t] * (l[p][t] + c[p][t] + s.bench_weight * (x[p][t] - l[p][t])) for p in P)
                            - s.hit_cost * hits[t]) for t in T) + s.ft_value * ft[len(T)]

    m += ft[0] == min(free_transfers, 5) if free_transfers <= 5 else ft[0] == 5
    for t in T:
        n = pulp.lpSum(bi[p][t] for p in P)
        prev_bank = bank if t == 0 else bank_v[t - 1]
        for p in P:
            prev = owned[p] if t == 0 else x[p][t - 1]
            m += x[p][t] == prev + bi[p][t] - so[p][t]
            m += bi[p][t] + so[p][t] <= 1
            m += l[p][t] <= x[p][t]
            m += c[p][t] <= l[p][t]
            if p in s.locked:
                m += x[p][t] == 1
            if p in s.banned and not owned[p]:
                m += bi[p][t] == 0
        m += bank_v[t] == prev_bank + pulp.lpSum(sell[p] * so[p][t] - buy[p] * bi[p][t] for p in P)
        for k, cnt in SQUAD.items():
            m += pulp.lpSum(x[p][t] for p in P if pos[p] == k) == cnt
            m += pulp.lpSum(l[p][t] for p in P if pos[p] == k) >= XI_MIN[k]
            m += pulp.lpSum(l[p][t] for p in P if pos[p] == k) <= XI_MAX[k]
        for cl in set(club.values()):
            m += pulp.lpSum(x[p][t] for p in P if club[p] == cl) <= 3
        m += pulp.lpSum(l[p][t] for p in P) == 11
        m += pulp.lpSum(c[p][t] for p in P) == 1
        if not allow_transfers:
            m += n == 0
        m += n <= s.max_transfers_per_gw
        # free transfers: hits cover anything beyond the free ones; leftovers roll (+1, max 5)
        m += hits[t] >= n - ft[t]
        m += ft[t + 1] <= ft[t] - n + hits[t] + 1

    solver = (pulp.HiGHS(msg=False, timeLimit=s.time_limit, gapRel=0.002)
              if "HiGHS" in pulp.listSolvers(onlyAvailable=True)
              else pulp.PULP_CBC_CMD(msg=False, timeLimit=s.time_limit, gapRel=0.002))
    m.solve(solver)
    if pulp.value(m.objective) is None or any(x[p][0].value() is None for p in P[:1]):
        raise RuntimeError(f"No valid plan found (solver status: {pulp.LpStatus[m.status]}). "
                           "Try a shorter planning window.")

    name = players["web_name"].to_dict()
    plan = []
    for t in T:
        ins = [p for p in P if bi[p][t].value() > 0.5]
        outs = [p for p in P if so[p][t].value() > 0.5]
        xi = [p for p in P if l[p][t].value() > 0.5]
        bench = [p for p in P if x[p][t].value() > 0.5 and p not in xi]
        cap = next(p for p in P if c[p][t].value() > 0.5)
        h = int(round(hits[t].value()))
        xi_pts = sum(pts[p, t] for p in xi) + pts[cap, t]
        moves = []
        for k in SQUAD:  # pair each sale with a purchase in the same position
            o_k = sorted((q for q in outs if pos[q] == k), key=lambda q: -sell[q])
            i_k = sorted((q for q in ins if pos[q] == k), key=lambda q: -buy[q])
            moves += [((o, name[o], sell[o]), (i, name[i], buy[i])) for o, i in zip(o_k, i_k)]
        plan.append({
            "gw": gws[t],
            "moves": moves,
            "free_transfers": int(round(ft[t].value())),
            "transfers_in": [(p, name[p], buy[p]) for p in ins],
            "transfers_out": [(p, name[p], sell[p]) for p in outs],
            "hits": h,
            "hit_cost": h * s.hit_cost,
            "bank": round(bank_v[t].value()),
            "captain": (cap, name[cap]),
            "xi": sorted(xi, key=lambda p: (pos[p], -pts[p, t])),
            "bench": sorted(bench, key=lambda p: (pos[p] != 1, -pts[p, t])),
            "xi_points": round(xi_pts, 2),
            "net_points": round(xi_pts - h * s.hit_cost, 2),
        })
    return {"plan": plan, "total_net": round(sum(p["net_points"] for p in plan), 2),
            "proj": proj, "objective": pulp.value(m.objective), "pool_size": len(P),
            "status": pulp.LpStatus[m.status], "solver_status": m.sol_status}


def single_transfers(players, projections, squad, selling, bank, gws, top=20) -> pd.DataFrame:
    """Simple ranking of 1-for-1 swaps by projected points gained over the horizon."""
    tot = projections[projections.gw.isin(gws)].groupby("fpl_id").pts.sum()
    clubs = players.loc[squad, "team"].value_counts()
    rows = []
    for out in squad:
        o = players.loc[out]
        money = bank + selling[out]
        cands = players[(players.element_type == o.element_type) & (players.now_cost <= money)
                        & ~players.index.isin(squad) & players.status.ne("u")]
        for pid, r in cands.iterrows():
            if r.team != o.team and clubs.get(r.team, 0) >= 3:
                continue
            gain = tot.get(pid, 0) - tot.get(out, 0)
            if gain > 0:
                rows.append({"out_id": out, "in_id": pid, "out": o.web_name, "in": r.web_name, "pos": r.pos, "in_team": r.team_short,
                             "in_price": r.now_cost / 10, "bank_after": (money - r.now_cost) / 10,
                             f"out_pts_{len(gws)}gw": round(tot.get(out, 0), 1),
                             f"in_pts_{len(gws)}gw": round(tot.get(pid, 0), 1), "gain": round(gain, 1)})
    df = pd.DataFrame(rows)
    return df.sort_values("gain", ascending=False).head(top).reset_index(drop=True) if len(df) else df
