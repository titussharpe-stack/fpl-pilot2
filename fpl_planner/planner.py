"""Glue: team + projections -> recommendations."""
from __future__ import annotations

from . import ffs, fpl_api
from .optimizer import Settings, single_transfers, solve
from .team import load_team


def fpl_fallback_projections(gws: list[int]):
    """Rough stand-in when no Scout snapshot exists: FPL's own ep_next repeated."""
    import pandas as pd
    boot = fpl_api.bootstrap()
    rows = [{"fpl_id": e["id"], "gw": gw, "pts": float(e["ep_next"] or 0)} for e in boot["elements"] for gw in gws]
    return pd.DataFrame(rows)


def recommend(team_id: int, settings: Settings = Settings(), ft_override: int | None = None,
              bank_override: int | None = None, allow_fallback: bool = False) -> dict:
    team = load_team(team_id)
    players = fpl_api.players_df()
    gws = list(range(team.next_gw, min(team.next_gw + settings.horizon, 39)))
    snap = ffs.latest_snapshot()
    if snap is not None and snap[0].gw.max() >= team.next_gw:
        proj, meta = snap
        source = f"Fantasy Football Scout (snapshot GW{meta.get('gw', '?')}, {meta.get('scraped_at', '')[:16]})"
    elif allow_fallback:
        proj, source = fpl_fallback_projections(gws), "FPL ep_next fallback (NOT Scout data)"
    else:
        raise ffs.ScoutError("No Scout projections for the upcoming gameweek yet. Run a scrape first.")
    ft = team.free_transfers if ft_override is None else ft_override
    bank = team.bank if bank_override is None else bank_override
    best = solve(players, proj, team.squad, team.selling_price, bank, ft, gws, settings)
    base = solve(players, proj, team.squad, team.selling_price, bank, ft, gws, settings, allow_transfers=False)
    singles = single_transfers(players, proj, team.squad, team.selling_price, bank, gws)
    return {"team": team, "players": players, "gws": gws, "source": source, "best": best,
            "baseline": base, "singles": singles, "free_transfers": ft, "bank": bank}
