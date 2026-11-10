"""HTML tables styled like Fantasy Football Scout's projections grid."""
from __future__ import annotations

import html

import pandas as pd

POS_LETTER = {1: "G", 2: "D", 3: "M", 4: "F"}
STOPS = [(0.0, (192, 40, 31)), (3.0, (110, 110, 70)), (6.0, (76, 182, 58))]  # red → olive → green

CSS = """
<style>
.sc-wrap{overflow-x:auto;margin:4px 0 18px}
.sc{border-collapse:collapse;font-family:inherit;font-size:15px;min-width:100%}
.sc th{padding:8px 10px;text-align:center;border-bottom:2px solid #2f8f5b;white-space:nowrap}
.sc th.l{text-align:left}
.sc td{padding:7px 10px;text-align:center;border-bottom:1px solid #2f8f5b;white-space:nowrap;color:#fff}
.sc td.name{background:#333;text-align:left;font-weight:500}
.sc td.team{background:#222}
.sc td.pos{background:#16243a;font-weight:600}
.sc td.tot{background:#1f3b2c;font-weight:700}
.sc td.stat{background:#262626;font-size:14px}
.sc th.stat{font-size:14px}
.sc td.none{background:#3a3a3a;color:#999}
.sc td.bench{opacity:.55}
.sc .cap{display:inline-block;margin-left:5px;padding:0 5px;border-radius:9px;background:#fff;color:#111;
         font-size:11px;font-weight:700;vertical-align:1px}
.sc .tag{font-size:11px;opacity:.85;margin-left:6px}
.sc tr.sum td{background:#111;font-weight:700;border-top:2px solid #2f8f5b}
.sc-key{font-size:13px;opacity:.75;margin-top:-10px}
</style>
"""


def colour(v: float) -> str:
    if pd.isna(v):
        return "#3a3a3a"
    if v <= STOPS[0][0]:
        r, g, b = STOPS[0][1]
        return f"rgb({r},{g},{b})"
    for (x0, c0), (x1, c1) in zip(STOPS, STOPS[1:]):
        if v <= x1:
            f = (v - x0) / (x1 - x0)
            r, g, b = (round(a + (b_ - a) * f) for a, b_ in zip(c0, c1))
            return f"rgb({r},{g},{b})"
    r, g, b = STOPS[-1][1]
    return f"rgb({r},{g},{b})"


def _e(x) -> str:
    return html.escape(str(x))


STAT_HEAD = ('<th class="stat" title="Selected by (% of managers)">TSB</th>'
             '<th class="stat" title="Total points this season">TP</th>'
             '<th class="stat" title="FPL form">Form</th>')


def stat_cells(players: pd.DataFrame, pid: int | None) -> str:
    """TSB, TP and Form cells for one player (blank cells if pid is None)."""
    if pid is None or pid not in players.index:
        return '<td class="stat"></td>' * 3
    r = players.loc[pid]
    return (f'<td class="stat">{r.tsb:.1f}%</td><td class="stat">{int(r.tp)}</td>'
            f'<td class="stat">{r.form_score:.1f}</td>')


def _head(gws, extra_first=("Player", "Team", "Pos")) -> str:
    cells = "".join(f'<th class="l">{_e(c)}</th>' if c == "Player" else f"<th>{_e(c)}</th>" for c in extra_first)
    return ("<thead><tr>" + cells + STAT_HEAD + "".join(f"<th>GW{g}</th>" for g in gws)
            + "<th>Total</th></tr></thead>")


def squad_grid(squad: list[int], players: pd.DataFrame, proj: pd.DataFrame, gws: list[int]) -> str:
    """Current squad: one row per player, one coloured cell per gameweek."""
    rows = []
    order = sorted(squad, key=lambda p: (players.at[p, "element_type"], -proj.loc[p, gws].sum()))
    for p in order:
        r = players.loc[p]
        vals = [float(proj.at[p, g]) if p in proj.index else float("nan") for g in gws]
        news = f'<span class="tag" title="{_e(r.news)}">⚠️</span>' if isinstance(r.news, str) and r.news else ""
        cells = "".join(f'<td style="background:{colour(v)}">{"" if pd.isna(v) else f"{v:.2f}"}</td>' for v in vals)
        rows.append(f'<tr><td class="name">{_e(r.web_name)}{news}</td><td class="team">{_e(r.team_name)}</td>'
                    f'<td class="pos">{POS_LETTER[r.element_type]}</td>{stat_cells(players, p)}{cells}'
                    f'<td class="tot">{sum(v for v in vals if not pd.isna(v)):.2f}</td></tr>')
    return CSS + f'<div class="sc-wrap"><table class="sc">{_head(gws)}<tbody>{"".join(rows)}</tbody></table></div>'


def plan_grid(plan: list[dict], start_squad: list[int], players: pd.DataFrame, proj: pd.DataFrame) -> str:
    """Team across the plan: who is owned each week, starters vs bench, captain, ins and outs."""
    gws = [p["gw"] for p in plan]
    owned = {p["gw"]: set(p["xi"]) | set(p["bench"]) for p in plan}
    everyone = list(dict.fromkeys(list(start_squad) + [q for p in plan for q in p["xi"] + p["bench"]]))
    joined = {i[0]: p["gw"] for p in plan for _, i in p["moves"]}
    left = {o[0]: p["gw"] for p in plan for o, _ in p["moves"]}

    def sort_key(q):
        weeks_owned = sum(q in owned[g] for g in gws)
        return (players.at[q, "element_type"], -weeks_owned, -sum(proj.at[q, g] for g in gws if q in owned[g]))

    rows = []
    for q in sorted(everyone, key=sort_key):
        r = players.loc[q]
        tag = ""
        if q in joined:
            tag += f'<span class="tag">in GW{joined[q]}</span>'
        if q in left:
            tag += f'<span class="tag">out GW{left[q]}</span>'
        cells, total = [], 0.0
        for p in plan:
            g = p["gw"]
            if q not in owned[g]:
                cells.append('<td class="none">–</td>')
                continue
            v = float(proj.at[q, g])
            starter = q in p["xi"]
            cap = q == p["captain"][0]
            total += v * (2 if cap else 1) if starter else 0
            badge = '<span class="cap">C</span>' if cap else ""
            cls = "" if starter else ' class="bench"'
            cells.append(f'<td{cls} style="background:{colour(v)}">{v:.2f}{badge}</td>')
        rows.append(f'<tr><td class="name">{_e(r.web_name)}{tag}</td><td class="team">{_e(r.team_name)}</td>'
                    f'<td class="pos">{POS_LETTER[r.element_type]}</td>{stat_cells(players, q)}{"".join(cells)}'
                    f'<td class="tot">{total:.2f}</td></tr>')
    hits = "".join(f"<td>{'-' + str(int(p['hit_cost'])) if p['hits'] else ''}</td>" for p in plan)
    team_tot = "".join(f"<td>{p['net_points']:.1f}</td>" for p in plan)
    rows.append(f'<tr class="sum"><td class="name" colspan="6">Hits</td>{hits}'
                f'<td>{-sum(p["hit_cost"] for p in plan):.0f}</td></tr>')
    rows.append(f'<tr class="sum"><td class="name" colspan="6">Team projected points</td>{team_tot}'
                f'<td>{sum(p["net_points"] for p in plan):.1f}</td></tr>')
    key = ('<div class="sc-key">Faded = on the bench · <b>C</b> = captain (points doubled in totals) · '
           '– = not in your squad that week · totals count starters only</div>')
    return CSS + f'<div class="sc-wrap"><table class="sc">{_head(gws)}<tbody>{"".join(rows)}</tbody></table></div>' + key


def gain_colour(v: float) -> str:
    """Diverging: red for losing points, grey around zero, green for gaining."""
    if pd.isna(v):
        return "#3a3a3a"
    f = max(-1.0, min(1.0, v / 3.0))
    grey = (85, 85, 85)
    end = (76, 182, 58) if f > 0 else (192, 40, 31)
    r, g, b = (round(a + (e - a) * abs(f)) for a, e in zip(grey, end))
    return f"rgb({r},{g},{b})"


def transfer_rows(plan: list[dict], proj: pd.DataFrame, hit_cost: float = 4.0) -> list[dict]:
    """Per planned transfer: weekly points of the player out / in from that week to the end of the plan.

    Hits are charged to the weakest moves of the week (those are the ones you pay for)."""
    gws = [p["gw"] for p in plan]
    out = []
    for p in plan:
        live = [g for g in gws if g >= p["gw"]]
        moves = []
        for o, i in p["moves"]:
            o_pts = {g: float(proj.at[o[0], g]) for g in live}
            i_pts = {g: float(proj.at[i[0], g]) for g in live}
            gain = {g: i_pts[g] - o_pts[g] for g in live}
            moves.append({"gw": p["gw"], "out": o, "in": i, "out_pts": o_pts, "in_pts": i_pts,
                          "gain": gain, "total": sum(gain.values()), "hit": 0.0})
        for m in sorted(moves, key=lambda m: m["total"])[: p["hits"]]:
            m["hit"] = hit_cost
        out += moves
    return out


def transfer_value_grid(plan: list[dict], players: pd.DataFrame, proj: pd.DataFrame, hit_cost: float = 4.0) -> str:
    gws = [p["gw"] for p in plan]
    moves = transfer_rows(plan, proj, hit_cost)
    if not moves:
        return CSS + '<div class="sc-key" style="margin-top:0">No transfers in this plan – rolling them is best.</div>'
    head = ("<thead><tr><th>When</th><th class='l'>Transfer</th><th>Pos</th>" + STAT_HEAD
            + "".join(f"<th>GW{g}</th>" for g in gws)
            + "<th>Total</th><th>Hit</th><th>Net gain</th></tr></thead>")
    rows = []
    for m in moves:
        o, i = m["out"], m["in"]
        pos = POS_LETTER[players.at[i[0], "element_type"]]
        span = ' rowspan="3"'
        o_cells = "".join(f'<td style="background:{colour(m["out_pts"][g])}">{m["out_pts"][g]:.2f}</td>'
                          if g in m["out_pts"] else '<td class="none"></td>' for g in gws)
        i_cells = "".join(f'<td style="background:{colour(m["in_pts"][g])}">{m["in_pts"][g]:.2f}</td>'
                          if g in m["in_pts"] else '<td class="none"></td>' for g in gws)
        g_cells = "".join(f'<td style="background:{gain_colour(m["gain"][g])};font-weight:700">'
                          f'{m["gain"][g]:+.2f}</td>' if g in m["gain"] else '<td class="none"></td>' for g in gws)
        net = m["total"] - m["hit"]
        hit = f"-{m['hit']:.0f}" if m["hit"] else ""
        rows.append(
            f'<tr><td class="pos"{span}>GW{m["gw"]}</td>'
            f'<td class="name">⬇️ {_e(o[1])} <span class="tag">£{o[2]/10:.1f} · {_e(players.at[o[0], "team_short"])}</span></td>'
            f'<td class="pos"{span}>{pos}</td>{stat_cells(players, o[0])}{o_cells}'
            f'<td class="tot">{sum(m["out_pts"].values()):.2f}</td>'
            f'<td class="tot"{span}>{hit}</td>'
            f'<td{span} style="background:{gain_colour(net)};font-weight:800;font-size:17px">{net:+.1f}</td></tr>'
            f'<tr><td class="name">⬆️ {_e(i[1])} <span class="tag">£{i[2]/10:.1f} · {_e(players.at[i[0], "team_short"])}</span></td>'
            f'{stat_cells(players, i[0])}{i_cells}<td class="tot">{sum(m["in_pts"].values()):.2f}</td></tr>'
            f'<tr><td class="name" style="background:#1b1b1b">Gain</td>{stat_cells(players, None)}{g_cells}'
            f'<td style="background:{gain_colour(m["total"])};font-weight:700">{m["total"]:+.2f}</td></tr>'
        )
    total_net = sum(m["total"] - m["hit"] for m in moves)
    rows.append(f'<tr class="sum"><td class="name" colspan="{6 + len(gws)}">All transfers together</td>'
                f'<td>{sum(m["total"] for m in moves):+.1f}</td><td>-{sum(m["hit"] for m in moves):.0f}</td>'
                f'<td>{total_net:+.1f}</td></tr>')
    key = ('<div class="sc-key">For each transfer: projected points of the player sold (⬇️) and bought (⬆️) '
           'each week from the transfer to the end of the plan, and the difference. A -4 hit is charged to the '
           'weakest move of that week. Captaincy and bench changes are not included, so the plan total can differ '
           'slightly.</div>')
    return CSS + f'<div class="sc-wrap"><table class="sc">{head}<tbody>{"".join(rows)}</tbody></table></div>' + key


def singles_grid(df: pd.DataFrame, horizon_label: str, players: pd.DataFrame) -> str:
    """Best single transfers, styled like the other tables, with TSB/TP/Form for both players."""
    if df is None or df.empty:
        return CSS + '<div class="sc-key">No single transfer gains points.</div>'
    out_c = [c for c in df.columns if c.startswith("out_pts")][0]
    in_c = [c for c in df.columns if c.startswith("in_pts")][0]
    head = ("<thead><tr><th class='l'>Out</th>" + STAT_HEAD + "<th class='l'>In</th>" + STAT_HEAD +
            "<th>Pos</th><th>Price</th><th>Bank after</th><th>Out pts</th><th>In pts</th>"
            f"<th>Gain {horizon_label}</th></tr></thead>")
    rows = "".join(
        f'<tr><td class="name">{_e(r["out"])}</td>{stat_cells(players, int(r["out_id"]))}'
        f'<td class="name">{_e(r["in"])} <span class="tag">{_e(r["in_team"])}</span></td>'
        f'{stat_cells(players, int(r["in_id"]))}<td class="pos">{_e(str(r["pos"])[:1])}</td>'
        f'<td class="team">£{r["in_price"]:.1f}</td><td class="team">£{r["bank_after"]:.1f}</td>'
        f'<td class="team">{r[out_c]:.1f}</td><td class="team">{r[in_c]:.1f}</td>'
        f'<td style="background:{gain_colour(r["gain"] / 2)};font-weight:700">{r["gain"]:+.1f}</td></tr>'
        for _, r in df.iterrows())
    return CSS + f'<div class="sc-wrap"><table class="sc">{head}<tbody>{rows}</tbody></table></div>'


def stats_table(players: pd.DataFrame) -> pd.DataFrame:
    """The FPL statistics table (fantasy.premierleague.com/statistics) as a DataFrame."""
    d = players.copy()
    d["net_tr"] = d.transfers_in_event - d.transfers_out_event
    out = pd.DataFrame({
        "Player": d.web_name, "Team": d.team_short, "Pos": d.pos, "£m": d.now_cost / 10,
        "TSB %": d.tsb, "TP": d.tp, "Form": d.form_score, "PPG": pd.to_numeric(d.points_per_game, errors="coerce"),
        "Mins": d.minutes, "G": d.goals_scored, "A": d.assists, "CS": d.clean_sheets, "Bonus": d.bonus,
        "xG": d.expected_goals, "xA": d.expected_assists, "ICT": d.ict_index, "Net transfers (GW)": d.net_tr,
        "News": d.news,
    })
    return out



def factor_summary(plans: list[dict], players: pd.DataFrame) -> str:
    """One row per factor: its transfers and how they score on Scout projections."""
    head = ("<thead><tr><th class='l'>Factor</th><th class='l'>Transfers (next 4 GWs)</th><th>Hits</th>"
            "<th>Gain next 4 GWs</th><th>Gain next 8 GWs</th><th>Gain rest of season</th></tr></thead>")
    rows = []
    for p in plans:
        moves = "<br>".join(f'<span class="tag" style="margin:0">GW{m["gw"]}</span> {_e(m["out"][1])} → '
                            f'<b>{_e(m["in"][1])}</b>' for m in p["moves"]) or "Roll transfers"
        e = p["eval"]
        rows.append(f'<tr><td class="name"><b>{_e(p["factor"])}</b></td>'
                    f'<td class="name" style="font-weight:400;line-height:1.7">{moves}</td>'
                    f'<td class="tot">{"-" + str(int(p["hits"])) if p["hits"] else ""}</td>'
                    + "".join(f'<td style="background:{gain_colour(e[k] / s)};font-weight:700">{e[k]:+.1f}</td>'
                              for k, s in (("4 weeks", 3), ("8 weeks", 5), ("Season", 12)))
                    + "</tr>")
    key = ('<div class="sc-key">Each factor picks its own transfers for the next 4 gameweeks. The gain columns '
           'score every plan on the same Scout projected points (after hits), so you can compare them.</div>')
    return CSS + f'<div class="sc-wrap"><table class="sc">{head}<tbody>{"".join(rows)}</tbody></table></div>' + key


def factor_plan_grid(plan: dict, players: pd.DataFrame, proj: pd.DataFrame, next_gw: int,
                     factor_vals: pd.Series, show_weeks: int = 8) -> str:
    """A factor's transfers with both players' projected points for the next 8 GWs and the season."""
    moves = plan["moves"]
    if not moves:
        return CSS + '<div class="sc-key">This factor recommends no transfers – roll them.</div>'
    gws = list(range(next_gw, min(next_gw + show_weeks, 39)))
    season = list(range(next_gw, 39))
    wide = proj[proj.gw >= next_gw].pivot_table(index="fpl_id", columns="gw", values="pts", aggfunc="sum")
    factor = plan["factor"]
    whole = "Total points" in factor or "Net" in factor

    def pts(pid, g):
        return float(wide.at[pid, g]) if pid in wide.index and g in wide.columns and pd.notna(wide.at[pid, g]) else 0.0

    def fv(pid):
        v = float(factor_vals.get(pid, 0.0))
        return f"{v:,.0f}" if whole else f"{v:.1f}"

    head = ("<thead><tr><th>When</th><th class='l'>Transfer</th><th>Pos</th>" + STAT_HEAD
            + f"<th>{_e(factor)}</th>" + "".join(f"<th>GW{g}</th>" for g in gws)
            + f"<th>{len(gws)}-GW total</th><th>Season total</th><th>Hit</th></tr></thead>")
    rows = []
    for m in moves:
        o, i, wk = m["out"][0], m["in"][0], m["gw"]
        span = ' rowspan="3"'
        pos = POS_LETTER[players.at[i, "element_type"]]

        def prow(pid, arrow, price, first):
            cells = "".join(f'<td style="background:{colour(pts(pid, g))}">{pts(pid, g):.2f}</td>' if g >= wk
                            else f'<td class="none">{pts(pid, g):.2f}</td>' for g in gws)
            t8 = sum(pts(pid, g) for g in gws if g >= wk)
            ts = sum(pts(pid, g) for g in season if g >= wk)
            pre = f'<td class="pos"{span}>GW{wk}</td>' if first else ""
            mid = f'<td class="pos"{span}>{pos}</td>' if first else ""
            hit = (f'<td class="tot"{span}>{"-" + str(int(m["hit"])) if m["hit"] else ""}</td>') if first else ""
            return (f'<tr>{pre}<td class="name">{arrow} {_e(players.at[pid, "web_name"])} '
                    f'<span class="tag">£{price/10:.1f} · {_e(players.at[pid, "team_short"])}</span></td>{mid}'
                    f'{stat_cells(players, pid)}<td class="team">{fv(pid)}</td>{cells}'
                    f'<td class="tot">{t8:.2f}</td><td class="tot">{ts:.1f}</td>{hit}</tr>')

        gain = {g: pts(i, g) - pts(o, g) for g in gws}
        g_cells = "".join(f'<td style="background:{gain_colour(gain[g])};font-weight:700">{gain[g]:+.2f}</td>'
                          if g >= wk else '<td class="none"></td>' for g in gws)
        g8 = sum(gain[g] for g in gws if g >= wk)
        gs = sum(pts(i, g) - pts(o, g) for g in season if g >= wk)
        rows.append(prow(o, "⬇️", m["out"][2], True) + prow(i, "⬆️", m["in"][2], False)
                    + f'<tr><td class="name" style="background:#1b1b1b">Gain</td>{stat_cells(players, None)}'
                      f'<td style="background:#1b1b1b"></td>{g_cells}'
                      f'<td style="background:{gain_colour(g8 / 2)};font-weight:700">{g8:+.2f}</td>'
                      f'<td style="background:{gain_colour(gs / 4)};font-weight:700">{gs:+.1f}</td></tr>')
    key = ('<div class="sc-key">Projected points are Scout\'s. Greyed weeks are before the transfer happens; '
           'totals count from the transfer week.</div>')
    return CSS + f'<div class="sc-wrap"><table class="sc">{head}<tbody>{"".join(rows)}</tbody></table></div>' + key
