"""nba-model command line.

    nba-model refresh                 # cache new finals (current + previous season)
    nba-model build --out _site       # board.json, slate.json, build.json, ledger.json, index.html
    nba-model status                  # authority and the backtest record
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from . import authority, bbref, bulk, export, ingest, ledger, nbastats, site


def _refresh(args: argparse.Namespace) -> int:
    season = export.current_season(datetime.now(timezone.utc))
    # A finished season is refreshed only when its cache is missing. The
    # current season's finals bring their stints (the player model's input).
    for s in range(season - 3, season):
        if not ingest.load_games(s) or not bulk.load_stints(s):
            bulk.run(s, workers=args.workers)
    bulk.run(season, workers=args.workers)
    # Basketball-Reference's current-season coaching and team pages (tenure,
    # mid-season changes, team shot distribution) - refreshed once a day.
    pages = ((bbref.fetch_coaches, bbref.coach_path(season)), (bbref.fetch_team_seasons, bbref.team_path(season)))
    for fetch, path in pages:
        stale_bb = not path.exists() or (datetime.now(timezone.utc).timestamp() - path.stat().st_mtime) > 20 * 3600
        if stale_bb:
            try:
                fetch(season, force=True)
            except Exception as exc:  # last snapshot stands
                print(f"bbref {season} {fetch.__name__}: {type(exc).__name__}: {exc}")
    # stats.nba.com (play types, tracking, defender dashboard, shot locations,
    # bio, staff) moves daily at most; it is refreshed every six hours, and a
    # refused pull leaves the last snapshot in place.
    _, fetched = nbastats.load(season, "synergy_T_offensive_Transition")
    stale = not fetched or (datetime.now(timezone.utc) - datetime.fromisoformat(fetched)).total_seconds() > 6 * 3600
    if stale:
        nbastats.refresh(season)
    return 0


def _build(args: argparse.Namespace) -> int:
    out = Path(args.out)
    if not args.no_refresh:
        _refresh(args)
    board = export.build(out=out)
    season = board["season"]
    rows = ledger.recover(out / "ledger.json")
    rows = ledger.grade(ledger.update(rows, board), ingest.load_games(season))
    record = ledger.write(out / "ledger.json", rows, board["generated_at_utc"])
    # The live gate is read from the ledger the board has just updated.
    board["live_signals"] = record["signals"]
    board["unmet_gates"] = authority.unmet(record["signals"])
    (out / "board.json").write_text(json.dumps(board, indent=1, default=str) + "\n", encoding="utf-8")
    (out / "index.html").write_text(site.render(board, record), encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")
    graded = record["summary"].get("graded", 0)
    print(f"board {board['slate_date']}: {len(board['games'])} games; ledger graded {graded}")
    return 0


def _status(_: argparse.Namespace) -> int:
    print(json.dumps({"authority": authority.LEVEL, "may_bet": False, "unmet_gates": authority.UNMET_GATES,
                      "evidence": authority.EVIDENCE}, indent=1))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="nba-model")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("refresh")
    r.add_argument("--workers", type=int, default=8)
    r.set_defaults(fn=_refresh)
    b = sub.add_parser("build")
    b.add_argument("--out", default="_site")
    b.add_argument("--workers", type=int, default=8)
    b.add_argument("--no-refresh", action="store_true")
    b.set_defaults(fn=_build)
    s = sub.add_parser("status")
    s.set_defaults(fn=_status)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
