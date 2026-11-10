"""Streamlit web app:  streamlit run app.py"""
import os
import time

import pandas as pd
import streamlit as st

from fpl_planner import ffs, fpl_api
from fpl_planner.config import DEBUG_DIR, get_ffs_credentials
from fpl_planner.optimizer import Settings
from fpl_planner.planner import recommend
from fpl_planner.ui_tables import plan_grid, singles_grid, squad_grid, transfer_value_grid

st.set_page_config(page_title="FPL Transfer Planner", page_icon="⚽", layout="wide")


def _load_secrets():
    """On Streamlit Cloud the login lives in Settings → Secrets (any capitalisation or section)."""
    try:
        flat = {}
        for k, v in st.secrets.items():
            if hasattr(v, "items"):
                flat.update({str(k2).upper(): v2 for k2, v2 in v.items()})
            else:
                flat[str(k).upper()] = v
        user = next((flat[k] for k in flat if "USER" in k or "EMAIL" in k), None)
        pw = next((flat[k] for k in flat if "PASS" in k), None)
        if user and pw:
            os.environ.setdefault("FFS_USERNAME", str(user).strip())
            os.environ.setdefault("FFS_PASSWORD", str(pw).strip())
    except Exception:
        pass


def run_scrape(force: bool):
    bar = st.progress(0.0, text="Logging in to Fantasy Football Scout…")
    try:
        res = ffs.scrape(force=force, progress=lambda f, t: bar.progress(min(f, 1.0), text=f"Pulling projections: {t}"))
        bar.empty()
        return res, None
    except Exception as e:
        bar.empty()
        return None, str(e)


_load_secrets()
st.title("FPL Transfer Planner")
st.caption("Your squad, budget and free transfers from FPL · projected points from Fantasy Football Scout Rate My Team")

has_login = bool(get_ffs_credentials())
snap = ffs.latest_snapshot()
next_gw = fpl_api.next_gw()
scrape_error = None

# Pull Scout data automatically once per gameweek
if has_login and (snap is None or snap[1].get("gw") != next_gw or snap[1].get("source") != "Rate My Team"):
    if "auto_tried" not in st.session_state:
        st.session_state.auto_tried = True
        _, scrape_error = run_scrape(force=True)
        snap = ffs.latest_snapshot()

if not has_login:
    st.error("**Scout login not found.** In Streamlit: ⋮ menu → Settings → Secrets, add\n\n"
             '`FFS_USERNAME = "your Scout email"`  \n`FFS_PASSWORD = "your Scout password"`\n\nthen Save.')
elif scrape_error:
    st.error(f"**Pulling Scout projections failed:** {scrape_error}")
    if (DEBUG_DIR / "last_page.html").exists():
        st.download_button("Download the Scout page for Claude to check", (DEBUG_DIR / "last_page.html").read_bytes(),
                           file_name="scout_page.html", mime="text/html")
elif snap:
    m = snap[1]
    gws_have = m.get("gameweeks") or sorted(snap[0].gw.unique())
    st.success(f"Scout projections loaded: GW{min(gws_have)}–GW{max(gws_have)} for "
               f"{m.get('matched_players', '?')} players (pulled {m.get('scraped_at', '')[:16].replace('T', ' ')} UTC).")

with st.sidebar:
    st.header("Projections")
    if has_login and st.button("Pull Scout projections now"):
        res, err = run_scrape(force=True)
        if err:
            st.error(err)
        else:
            st.rerun()
    if snap and snap[1].get("unmatched"):
        with st.expander(f"{len(snap[1]['unmatched'])} Scout names not matched to FPL"):
            st.write(", ".join(map(str, snap[1]["unmatched"])))
    fallback = st.checkbox("If no Scout data, use FPL's own estimate", value=snap is None)

    st.header("Planner settings")
    max_weeks = max(1, 39 - (next_gw or 38))
    if snap:
        max_weeks = max(1, len([g for g in snap[0].gw.unique() if g >= (next_gw or 0)]))
    horizon = st.slider("Weeks to plan ahead", 1, max_weeks, min(4, max_weeks),
                        help="The plan maximises total projected points over this many gameweeks.")
    max_hits = st.slider("Max -4 hits per gameweek", 0, 3, 1)
    bench_w = st.slider("Bench points weight", 0.0, 0.5, 0.1, 0.05)

team_id = st.text_input("FPL team ID", value=st.query_params.get("team", ""),
                        help="The number in your FPL points page URL, e.g. 2804900")

tab_plan, tab_data = st.tabs(["Transfer plan", "Projections by gameweek"])

with tab_data:
    if snap:
        proj = snap[0]
        players = fpl_api.players_df()
        gws_all = sorted(g for g in proj.gw.unique() if g >= (next_gw or 0))
        lo, hi = st.select_slider("Gameweeks", options=gws_all, value=(gws_all[0], gws_all[min(len(gws_all) - 1, 3)]))
        sel = proj[(proj.gw >= lo) & (proj.gw <= hi)]
        wide = sel.pivot_table(index="fpl_id", columns="gw", values="pts", aggfunc="sum")
        wide.columns = [f"GW{c}" for c in wide.columns]
        wide["Total"] = wide.sum(axis=1)
        info = players.loc[players.index.intersection(wide.index), ["web_name", "team_short", "pos", "now_cost"]]
        info = info.rename(columns={"web_name": "Player", "team_short": "Team", "pos": "Pos", "now_cost": "£m"})
        info["£m"] = info["£m"] / 10
        table = info.join(wide.round(2)).sort_values("Total", ascending=False)
        pos = st.multiselect("Positions", ["GKP", "DEF", "MID", "FWD"], default=["GKP", "DEF", "MID", "FWD"])
        st.dataframe(table[table.Pos.isin(pos)], hide_index=True, use_container_width=True, height=600)
    else:
        st.info("No Scout projections yet.")

with tab_plan:
    if st.button("Review my team", type="primary") and team_id.strip().isdigit():
        st.query_params["team"] = team_id.strip()
        s = Settings(horizon=horizon, max_hits_per_gw=max_hits, bench_weight=bench_w)
        with st.spinner(f"Loading team and optimising {horizon} gameweek(s)…"):
            try:
                t0 = time.time()
                r = recommend(int(team_id), s, allow_fallback=fallback)
                took = time.time() - t0
            except Exception as e:
                st.error(str(e))
                st.stop()
        t, players, gws = r["team"], r["players"], r["gws"]
        st.subheader(f"{t.name}" + (f" · {t.manager}" if t.manager else ""))
        m = st.columns(5)
        m[0].metric("Next gameweek", f"GW{t.next_gw}")
        m[1].metric("In the bank", f"£{r['bank']/10:.1f}m")
        m[2].metric("Squad selling value", f"£{t.squad_value/10:.1f}m")
        m[3].metric("Total budget", f"£{(r['bank'] + t.squad_value)/10:.1f}m")
        m[4].metric("Free transfers", r["free_transfers"])
        for n in t.notes:
            st.info(n)
        (st.warning if "fallback" in r["source"] else st.caption)(f"Projections: {r['source']}")

        best, base = r["best"], r["baseline"]
        gain = best["total_net"] - base["total_net"]
        st.markdown(f"### Plan for GW{gws[0]}–GW{gws[-1]} · **{best['total_net']:.1f}** projected pts "
                    f"(**{gain:+.1f}** vs no transfers)")
        if best["status"] != "Optimal":
            st.caption("Solver hit its time limit – this is the best plan found, very close to optimal.")
        st.caption(f"Considered {best['pool_size']} candidate players · solved in {took:.0f}s")
        proj = best["proj"]
        st.markdown("### Value of each transfer")
        st.markdown(transfer_value_grid(best["plan"], players, proj, s.hit_cost), unsafe_allow_html=True)
        st.markdown("### Week by week")
        for p in best["plan"]:
            moves = [f"**{o[1]}** (£{o[2]/10:.1f}) → **{i[1]}** (£{i[2]/10:.1f})" for o, i in p["moves"]]
            hit = f" · ⚠️ -{int(p['hit_cost'])} hit" if p["hits"] else ""
            with st.expander(f"GW{p['gw']} — {len(moves) or 'no'} transfer(s){hit} · captain {p['captain'][1]} · "
                             f"{p['net_points']:.1f} pts · {p['free_transfers']} FT available",
                             expanded=p is best["plan"][0]):
                st.markdown("\n".join(f"- {mv}" for mv in moves) or "Save the transfer (roll it over).")
                st.caption(f"Bank after: £{p['bank']/10:.1f}m")
                rows = [{"Pos": players.at[q, "pos"], "Player": players.at[q, "web_name"],
                         "Team": players.at[q, "team_short"], "Proj": round(proj.at[q, p['gw']], 2),
                         "Role": "C" if q == p["captain"][0] else ("XI" if q in p["xi"] else "Bench")}
                        for q in p["xi"] + p["bench"]]
                st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

        st.markdown(f"### Your team over GW{gws[0]}–GW{gws[-1]} with the planned transfers")
        st.markdown(plan_grid(best["plan"], t.squad, players, proj), unsafe_allow_html=True)

        st.markdown(f"### Current squad projections (GW{gws[0]}–GW{gws[-1]}, no transfers)")
        st.markdown(squad_grid(t.squad, players, proj, gws), unsafe_allow_html=True)

        st.markdown(f"### Best single transfers (projected gain over GW{gws[0]}–GW{gws[-1]})")
        st.markdown(singles_grid(r["singles"], f"GW{gws[0]}–{gws[-1]}"), unsafe_allow_html=True)
