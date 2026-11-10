import pandas as pd
import pytest

from fpl_planner import ffs
from fpl_planner.optimizer import Settings, solve
from fpl_planner.team import compute_free_transfers, selling_price


def test_selling_price():
    assert selling_price(60, 63) == 61   # keep half the rise, rounded down
    assert selling_price(60, 64) == 62
    assert selling_price(60, 58) == 58   # falls hit you in full


def test_free_transfers_roll_and_cap():
    hist = [{"event": g, "event_transfers": 0} for g in range(1, 11)]
    assert compute_free_transfers(hist, [], 10) == 5          # capped at 5
    hist[2]["event_transfers"] = 2                             # GW3: 2 transfers with 2 FT
    assert compute_free_transfers(hist[:3], [], 3) == 1
    hist2 = [{"event": 1, "event_transfers": 0}, {"event": 2, "event_transfers": 3}]
    assert compute_free_transfers(hist2, [], 2) == 1           # hits don't go negative
    chips = [{"event": 2, "name": "wildcard"}]
    hist3 = [{"event": 1, "event_transfers": 0}, {"event": 2, "event_transfers": 9}]
    assert compute_free_transfers(hist3, chips, 2) == 2        # wildcard keeps FTs


def _players():
    rows, pid = [], 1
    for pos, n in {1: 3, 2: 7, 3: 7, 4: 5}.items():
        for i in range(n):
            rows.append(dict(id=pid, web_name=f"P{pid}", first_name="A", second_name=f"P{pid}",
                             team=pid % 10 + 1, element_type=pos, now_cost=50, status="a", pos=str(pos)))
            pid += 1
    return pd.DataFrame(rows).set_index("id")


def test_optimizer_makes_obvious_transfer():
    players = _players()
    squad = [1, 2, 4, 5, 6, 7, 8, 11, 12, 13, 14, 15, 18, 19, 20]
    gws = [11, 12]
    proj = pd.DataFrame([{"fpl_id": p, "gw": g, "pts": 2.0} for p in players.index for g in gws])
    proj.loc[proj.fpl_id == 16, "pts"] = 10.0   # unowned MID who will score big
    res = solve(players, proj, squad, {p: 50 for p in squad}, bank=0, free_transfers=1, gws=gws,
                s=Settings(pool_per_pos=50))
    first = res["plan"][0]
    assert [i[0] for i in first["transfers_in"]] == [16]
    assert first["hits"] == 0 and first["captain"][0] == 16


HTML = """<html><body><table>
<thead><tr><th>Player</th><th>Team</th><th>Pos</th><th>Price</th><th>GW11</th><th>GW12</th><th>Total</th></tr></thead>
<tbody>
<tr><td>Haaland</td><td>Man City</td><td>FWD</td><td>£14.5</td><td>7.1</td><td>6.4</td><td>200</td></tr>
<tr><td>M.Salah</td><td>Liverpool</td><td>MID</td><td>£13.0</td><td>6.0</td><td>5.5</td><td>190</td></tr>
</tbody></table></body></html>"""


def test_parse_and_match():
    wide = ffs.parse_tables(HTML)
    assert list(wide["name"]) == ["Haaland", "M.Salah"] and 11 in wide and 12 in wide
    players = pd.DataFrame([
        dict(id=10, web_name="Haaland", first_name="Erling", second_name="Haaland", team=13,
             team_name="Man City", team_short="MCI", element_type=4),
        dict(id=20, web_name="M.Salah", first_name="Mohamed", second_name="Salah", team=12,
             team_name="Liverpool", team_short="LIV", element_type=3),
    ]).set_index("id")
    long, unmatched = ffs.to_long(wide, players, next_gw=11)
    assert unmatched.empty
    assert long.set_index(["fpl_id", "gw"]).pts.to_dict() == {(10, 11): 7.1, (10, 12): 6.4, (20, 11): 6.0, (20, 12): 5.5}


def test_login_page_detected():
    assert ffs._is_login_page('<input name="password"> <button id="loginButton">')
