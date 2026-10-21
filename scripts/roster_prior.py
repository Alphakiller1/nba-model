#!/usr/bin/env python3
"""Does re-weighting last season's production to this season's roster improve the prior?

The roster for a backtest season is the set of players who appeared for each
club in its first ten games (the closest stand-in for an opening-night roster
the box-score cache holds). The scale is chosen on 2024-25 and scored once on
2025-26, over the first three and first six weeks, where the prior matters.
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nbamodel import availability as av  # noqa: E402
from nbamodel import backtest, ingest  # noqa: E402
from nbamodel.ratings import CONSTANTS, PARAMS  # noqa: E402


def roster_diff(season: int) -> dict[str, float]:
    prev = av.lines(ingest.load_players(season - 1))
    vals = av.season_values(prev, av.lines(ingest.load_players(season - 2)))
    last = av.last_team_value(prev, vals)
    games = ingest.load_games(season)
    first: dict[str, list[str]] = defaultdict(list)
    for g in games:
        for t in (g.home, g.away):
            if len(first[t]) < 10:
                first[t].append(g.event_id)
    keep = {(e, t) for t, es in first.items() for e in es}
    roster: dict[str, set[str]] = defaultdict(set)
    for ln in av.lines(ingest.load_players(season)):
        if (ln.event_id, ln.team) in keep:
            roster[ln.team].add(ln.player)
    return {t: av.roster_value(roster[t], vals) - last.get(t, 0.0) for t in roster}


def main() -> int:
    seasons = {s: ingest.load_games(s) for s in (2024, 2025, 2026)}
    seed = backtest.season_end(seasons[2024], backtest.prior_from(None, PARAMS), PARAMS, CONSTANTS)
    end25 = backtest.season_end(seasons[2025], backtest.prior_from(seed, PARAMS), PARAMS, CONSTANTS)
    prevs = {2025: seed, 2026: end25}
    diffs = {s: roster_diff(s) for s in (2025, 2026)}
    for s in (2025, 2026):
        d = sorted(diffs[s].items(), key=lambda kv: kv[1])
        print(s, "largest roster losses:", [(t, round(v, 3)) for t, v in d[:3]],
              "gains:", [(t, round(v, 3)) for t, v in d[-3:]])
    for k in (0, 20, 40, 60, 80, 100):
        out = []
        for s in (2025, 2026):
            rows = backtest.walk_forward(seasons[s], prevs[s], PARAMS, CONSTANTS,
                                         shift={t: k * v for t, v in diffs[s].items()})
            w3 = backtest.summarize([r for r in rows if r.day_index < 21])
            w6 = backtest.summarize([r for r in rows if r.day_index < 42])
            full = backtest.summarize(rows)
            out.append(f"{s}: wk1-3 {w3['margin_mae']:.3f}  wk1-6 {w6['margin_mae']:.3f}  "
                       f"season {full['margin_mae']:.3f}")
        print(f"k={k:>3}  " + "   |   ".join(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
