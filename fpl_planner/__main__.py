"""Command line:
  python -m fpl_planner login              store Scout login in the OS password vault
  python -m fpl_planner logout             remove it
  python -m fpl_planner scrape [--force]   pull projections for the next GW (once per GW)
  python -m fpl_planner scrape --if-due    only scrape inside the window before the deadline (for cron)
  python -m fpl_planner schedule           keep running; scrape once every gameweek
  python -m fpl_planner plan 2804900       print transfer plan
"""
from __future__ import annotations

import argparse
import getpass
import sys
import time
from datetime import datetime, timedelta, timezone

from . import ffs, fpl_api
from .config import delete_ffs_credentials, get_ffs_credentials, save_ffs_credentials
from .optimizer import Settings
from .planner import recommend


def cmd_login(_):
    user = input("Fantasy Football Scout email: ").strip()
    pw = getpass.getpass("Password (hidden): ")
    print("Checking login…")
    ffs.login(user, pw)
    save_ffs_credentials(user, pw)
    print("Login OK – saved in your system password vault (not in any file).")


def cmd_logout(_):
    delete_ffs_credentials()
    print("Removed stored Scout login.")


def scrape_due(hours_before: float) -> bool:
    ev = fpl_api.events()
    nxt = fpl_api.next_gw()
    if nxt is None:
        return False
    if ffs.snapshot_path(nxt).exists():
        return False
    return datetime.now(timezone.utc) >= ev.loc[nxt, "deadline_time"] - timedelta(hours=hours_before)


def cmd_scrape(a):
    if a.if_due and not scrape_due(a.hours_before):
        print("Not due yet (or already scraped this gameweek).")
        return
    res = ffs.scrape(force=a.force)
    if res["skipped"]:
        print(f"Already have GW{res['gw']} projections ({res['path']}). Use --force to refresh.")
    else:
        print(f"Saved GW{res['gw']} projections: {res['matched_players']} players matched, "
              f"{len(res['unmatched'])} unmatched -> {res['path']}")
        if res["unmatched"]:
            print("Unmatched (first 20):", ", ".join(map(str, res["unmatched"][:20])))


def cmd_schedule(a):
    print(f"Scheduler running: scrapes once per gameweek, {a.hours_before}h before the deadline. Ctrl+C to stop.")
    while True:
        try:
            if scrape_due(a.hours_before):
                cmd_scrape(argparse.Namespace(if_due=False, force=False, hours_before=a.hours_before))
        except Exception as e:  # keep the loop alive
            print(f"[{datetime.now():%Y-%m-%d %H:%M}] scrape failed: {e}", file=sys.stderr)
        time.sleep(a.every_minutes * 60)


def cmd_plan(a):
    s = Settings(horizon=a.horizon, max_hits_per_gw=a.max_hits)
    r = recommend(a.team_id, s, ft_override=a.ft, allow_fallback=a.fallback)
    t = r["team"]
    print(f"{t.name} – next GW{t.next_gw}")
    print(f"Bank £{r['bank']/10:.1f}m | squad selling value £{t.squad_value/10:.1f}m | "
          f"total budget £{(r['bank']+t.squad_value)/10:.1f}m | free transfers {r['free_transfers']}")
    for n in t.notes:
        print("  note:", n)
    print(f"Projections: {r['source']}\n")
    for p in r["best"]["plan"]:
        moves = ", ".join(f"{o[1]} -> {i[1]}" for o, i in p["moves"]) or "roll transfer"
        print(f"GW{p['gw']} (FT {p['free_transfers']}): {moves}"
              f"{'  [-' + str(int(p['hit_cost'])) + ']' if p['hits'] else ''} | C: {p['captain'][1]} | "
              f"proj {p['net_points']} | bank £{p['bank']/10:.1f}m")
    gain = r["best"]["total_net"] - r["baseline"]["total_net"]
    print(f"\nPlan: {r['best']['total_net']:.1f} pts vs {r['baseline']['total_net']:.1f} with no transfers (+{gain:.1f})")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fpl_planner")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("login").set_defaults(fn=cmd_login)
    sub.add_parser("logout").set_defaults(fn=cmd_logout)
    sp = sub.add_parser("scrape")
    sp.add_argument("--force", action="store_true")
    sp.add_argument("--if-due", action="store_true")
    sp.add_argument("--hours-before", type=float, default=30)
    sp.set_defaults(fn=cmd_scrape)
    sc = sub.add_parser("schedule")
    sc.add_argument("--hours-before", type=float, default=30)
    sc.add_argument("--every-minutes", type=int, default=60)
    sc.set_defaults(fn=cmd_schedule)
    pp = sub.add_parser("plan")
    pp.add_argument("team_id", type=int)
    pp.add_argument("--horizon", type=int, default=4)
    pp.add_argument("--max-hits", type=int, default=1)
    pp.add_argument("--ft", type=int, default=None, help="override free transfers")
    pp.add_argument("--fallback", action="store_true", help="use FPL ep_next if no Scout data")
    pp.set_defaults(fn=cmd_plan)
    a = ap.parse_args(argv)
    try:
        a.fn(a)
    except ffs.ScoutError as e:
        sys.exit(f"Error: {e}")


if __name__ == "__main__":
    main()
