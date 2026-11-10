"""Streamlit web app:  streamlit run app.py"""
import pandas as pd
import streamlit as st

from fpl_planner import ffs
from fpl_planner.config import get_ffs_credentials
from fpl_planner.optimizer import Settings
from fpl_planner.planner import recommend

st.set_page_config(page_title="FPL Transfer Planner", page_icon="⚽", layout="wide")
st.title("FPL Transfer Planner")
st.caption("Your squad, budget and free transfers from FPL · projections from Fantasy Football Scout")

@st.cache_data(ttl=3600, show_spinner=False)
def auto_scrape(_hour: int):
    """Pull Scout projections automatically once per gameweek (hosted version has no scheduler)."""
    try:
        res = ffs.scrape(force=False)
        return None
    except Exception as e:
        return str(e)


if get_ffs_credentials():
    import time
    with st.spinner("Checking for this gameweek's Scout projections…"):
        scrape_error = auto_scrape(int(time.time() // 3600))
else:
    scrape_error = None

with st.sidebar:
    st.header("Projections")
    if scrape_error:
        st.error(f"Automatic Scout update failed: {scrape_error}")
    snap = ffs.latest_snapshot()
    if snap:
        meta = snap[1]
        st.success(f"Scout snapshot GW{meta.get('gw')} · {meta.get('scraped_at', '')[:16].replace('T', ' ')} UTC\n\n"
                   f"{meta.get('matched_players', '?')} players matched")
        if meta.get("unmatched"):
            with st.expander(f"{len(meta['unmatched'])} unmatched names"):
                st.write(", ".join(map(str, meta["unmatched"])))
    else:
        st.warning("No Scout projections yet.")
    if get_ffs_credentials():
        if st.button("Refresh projections now"):
            with st.spinner("Logging in to Scout and scraping…"):
                try:
                    res = ffs.scrape(force=True)
                    st.success(f"Saved GW{res['gw']}: {res['matched_players']} players")
                    st.rerun()
                except Exception as e:
                    st.error(str(e))
    else:
        st.info("Add your Scout login: on the hosted site use the app's Settings → Secrets; "
                "on your own computer run `python -m fpl_planner login`.")
    fallback = st.checkbox("Use FPL's own estimate if no Scout data", value=snap is None)

    st.header("Planner settings")
    horizon = st.slider("Gameweeks to plan", 1, 6, 4)
    max_hits = st.slider("Max -4 hits per gameweek", 0, 3, 1)
    bench_w = st.slider("Bench points weight", 0.0, 0.5, 0.1, 0.05)
    decay = st.slider("Weight on later weeks", 0.7, 1.0, 1.0, 0.05,
                      help="1.0 = every week counts the same; lower trusts near weeks more")
    ft_value = st.slider("Value of a saved free transfer (pts)", 0.0, 3.0, 0.0, 0.5)

team_id = st.text_input("FPL team ID", value=st.query_params.get("team", ""),
                        help="The number in your FPL points page URL, e.g. 2804900")
c1, c2 = st.columns(2)
ft_override = c1.number_input("Free transfers (leave 0 = auto)", 0, 5, 0)
bank_override = c2.number_input("Bank £m (leave 0 = auto)", 0.0, 100.0, 0.0, 0.1)

if st.button("Review my team", type="primary") and team_id.strip().isdigit():
    st.query_params["team"] = team_id.strip()
    s = Settings(horizon=horizon, max_hits_per_gw=max_hits, bench_weight=bench_w, decay=decay, ft_value=ft_value)
    with st.spinner("Loading team and optimising…"):
        try:
            r = recommend(int(team_id), s, ft_override=ft_override or None,
                          bank_override=round(bank_override * 10) or None, allow_fallback=fallback)
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
    if "fallback" in r["source"]:
        st.warning(f"Projections: {r['source']}")
    else:
        st.caption(f"Projections: {r['source']}")

    best, base = r["best"], r["baseline"]
    gain = best["total_net"] - base["total_net"]
    st.markdown(f"### Recommended plan · **{best['total_net']:.1f}** projected pts over {len(gws)} GWs "
                f"(**{gain:+.1f}** vs no transfers)")
    for p in best["plan"]:
        moves = [f"**{o[1]}** (£{o[2]/10:.1f}) → **{i[1]}** (£{i[2]/10:.1f})"
                 for o, i in p["moves"]]
        hit = f" · ⚠️ -{int(p['hit_cost'])} hit" if p["hits"] else ""
        with st.expander(f"GW{p['gw']} — {len(moves) or 'no'} transfer(s){hit} · captain {p['captain'][1]} · "
                         f"{p['net_points']:.1f} pts · {p['free_transfers']} FT available", expanded=p is best["plan"][0]):
            st.markdown("\n".join(f"- {mv}" for mv in moves) or "Save the transfer (roll it over).")
            st.caption(f"Bank after: £{p['bank']/10:.1f}m")
            proj = best["proj"]
            rows = [{"Pos": players.at[q, "pos"], "Player": players.at[q, "web_name"],
                     "Team": players.at[q, "team_short"], "Proj": round(proj.at[q, p['gw']], 2),
                     "Role": "C" if q == p["captain"][0] else ("XI" if q in p["xi"] else "Bench")}
                    for q in p["xi"] + p["bench"]]
            st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

    st.markdown("### Current squad projections")
    proj = best["proj"]
    sq = pd.DataFrame([{"Pos": players.at[q, "pos"], "Player": players.at[q, "web_name"],
                        "Team": players.at[q, "team_short"], "Sell £m": t.selling_price[q] / 10,
                        **{f"GW{g}": round(proj.at[q, g], 1) for g in gws},
                        "Total": round(proj.loc[q].sum(), 1), "News": players.at[q, "news"]} for q in t.squad])
    st.dataframe(sq.sort_values(["Pos", "Total"], ascending=[True, False]), hide_index=True, use_container_width=True)

    st.markdown(f"### Best single transfers (by {len(gws)}-GW projected gain)")
    st.dataframe(r["singles"], hide_index=True, use_container_width=True)
