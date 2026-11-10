"""Work out a manager's squad, budget and free transfers from their team ID.

Everything comes from the public FPL API, so a few things are estimates:
- Selling prices use FPL's rule (you keep half of any rise, rounded down),
  based on the price paid from the transfer history.
- Free transfers are rebuilt from history: +1 per gameweek, max 5,
  saved transfers kept on Wildcard / Free Hit weeks. Special one-off
  top-ups announced by FPL are not known to the API, so the UI lets you override.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import fpl_api

MAX_FT = 5
FT_KEEPING_CHIPS = {"wildcard", "freehit"}


@dataclass
class TeamState:
    team_id: int
    name: str
    manager: str
    next_gw: int
    squad: list[int]                      # 15 FPL element ids
    selling_price: dict[int, int]         # tenths of £m
    purchase_price: dict[int, int]
    bank: int                             # tenths of £m
    free_transfers: int
    pending_transfers: list[dict] = field(default_factory=list)
    chips_used: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def squad_value(self) -> int:
        return sum(self.selling_price[p] for p in self.squad)

    @property
    def budget(self) -> int:
        return self.bank + self.squad_value


def selling_price(purchase: int, now: int) -> int:
    return now if now <= purchase else purchase + (now - purchase) // 2


def compute_free_transfers(history_current: list[dict], chips: list[dict], upto_gw: int) -> int:
    """Free transfers available for gameweek `upto_gw + 1`."""
    chip_by_gw = {c["event"]: c["name"] for c in chips}
    rows = {h["event"]: h for h in history_current}
    started = min(rows) if rows else upto_gw + 1
    ft = 1  # available in the gameweek after the team's first gameweek
    for gw in range(started + 1, upto_gw + 1):
        made = rows.get(gw, {}).get("event_transfers", 0)
        if chip_by_gw.get(gw) not in FT_KEEPING_CHIPS:
            ft = max(ft - made, 0)
        ft = min(ft + 1, MAX_FT)
    return ft


def load_team(team_id: int) -> TeamState:
    boot = fpl_api.bootstrap()
    players = fpl_api.players_df(boot)
    nxt = fpl_api.next_gw(boot)
    if nxt is None:
        raise ValueError("The season has finished – no upcoming gameweek.")
    ent = fpl_api.entry(team_id)
    hist = fpl_api.history(team_id)
    trs = fpl_api.transfers(team_id)
    notes: list[str] = []

    played = [h["event"] for h in hist["current"]]
    last_gw = max(played) if played else None
    chip_by_gw = {c["event"]: c["name"] for c in hist["chips"]}

    if last_gw is None:
        raise ValueError("This team hasn't played a gameweek yet.")

    # A Free Hit squad reverts, so read the squad from the gameweek before it.
    squad_gw = last_gw
    while chip_by_gw.get(squad_gw) == "freehit" and squad_gw > 1:
        squad_gw -= 1
    pk = fpl_api.picks(team_id, squad_gw)
    squad = [p["element"] for p in pk["picks"]]
    bank = pk["entry_history"]["bank"]
    if squad_gw != last_gw:
        notes.append(f"GW{last_gw} was a Free Hit, so the squad from GW{squad_gw} is used.")

    # Transfers already made for the upcoming gameweek are not in the picks yet.
    fh_gws = {gw for gw, c in chip_by_gw.items() if c == "freehit"}
    pending = [t for t in trs if t["event"] > last_gw]
    for t in sorted(pending, key=lambda t: t["time"]):
        if t["element_out"] in squad:
            squad[squad.index(t["element_out"])] = t["element_in"]
            bank += t["element_out_cost"] - t["element_in_cost"]
    if pending:
        notes.append(f"{len(pending)} transfer(s) already made for GW{nxt} are included.")

    # Purchase price = cost on the most recent transfer in, else start-of-season price.
    purchase, sell = {}, {}
    for pid in squad:
        buys = [t for t in trs if t["element_in"] == pid and t["event"] not in fh_gws]
        if buys:
            purchase[pid] = max(buys, key=lambda t: t["time"])["element_in_cost"]
        else:
            row = players.loc[pid]
            purchase[pid] = int(row.now_cost - row.cost_change_start)
        sell[pid] = selling_price(purchase[pid], int(players.loc[pid].now_cost))

    ft = compute_free_transfers(hist["current"], hist["chips"], last_gw)
    if pending:
        ft = max(ft - len(pending), 0)
        notes.append("Free transfers shown are what's left after those transfers.")
    if nxt == 1 or last_gw == 0:
        ft = 15

    return TeamState(
        team_id=team_id,
        name=ent["name"],
        manager=f'{ent.get("player_first_name", "")} {ent.get("player_last_name", "")}'.strip(),
        next_gw=nxt,
        squad=squad,
        selling_price=sell,
        purchase_price=purchase,
        bank=bank,
        free_transfers=ft,
        pending_transfers=pending,
        chips_used=[f'{c["name"]} (GW{c["event"]})' for c in hist["chips"]],
        notes=notes,
    )
