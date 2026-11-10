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


def _head(gws, extra_first=("Player", "Team", "Pos")) -> str:
    cells = "".join(f'<th class="l">{_e(c)}</th>' if c == "Player" else f"<th>{_e(c)}</th>" for c in extra_first)
    return "<thead><tr>" + cells + "".join(f"<th>GW{g}</th>" for g in gws) + "<th>Total</th></tr></thead>"


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
                    f'<td class="pos">{POS_LETTER[r.element_type]}</td>{cells}'
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
                    f'<td class="pos">{POS_LETTER[r.element_type]}</td>{"".join(cells)}'
                    f'<td class="tot">{total:.2f}</td></tr>')
    hits = "".join(f"<td>{'-' + str(int(p['hit_cost'])) if p['hits'] else ''}</td>" for p in plan)
    team_tot = "".join(f"<td>{p['net_points']:.1f}</td>" for p in plan)
    rows.append(f'<tr class="sum"><td class="name" colspan="3">Hits</td>{hits}'
                f'<td>{-sum(p["hit_cost"] for p in plan):.0f}</td></tr>')
    rows.append(f'<tr class="sum"><td class="name" colspan="3">Team projected points</td>{team_tot}'
                f'<td>{sum(p["net_points"] for p in plan):.1f}</td></tr>')
    key = ('<div class="sc-key">Faded = on the bench · <b>C</b> = captain (points doubled in totals) · '
           '– = not in your squad that week · totals count starters only</div>')
    return CSS + f'<div class="sc-wrap"><table class="sc">{_head(gws)}<tbody>{"".join(rows)}</tbody></table></div>' + key
