#!/usr/bin/env python3
"""Choose the rating hyperparameters on the tuning season, then score the holdout.

    python scripts/tune.py                 # tune on 2025 (prior 2024), holdout 2026
    python scripts/tune.py --quick         # small grid

The tuning season picks the parameters by margin + total MAE only (2024-25
carries no stored closing lines). The holdout season is scored once with the
chosen parameters, and is the only season that meets the market.
"""
from __future__ import annotations

import argparse
import itertools
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nbamodel import availability, backtest, calibrate, ingest  # noqa: E402
from nbamodel.ratings import Constants, Params  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    args = ap.parse_args()
    g24, g25, g26 = (ingest.load_games(s) for s in (2024, 2025, 2026))
    c = calibrate.fit_constants([g24, g25])
    print("constants (2024+2025):", json.dumps(c))
    const = Constants(c["home_eff"], c["b2b_off"], c["b2b_def"], 12.5, 18.0)

    grid = dict(half_life_days=[45, 90, 180], ridge=[3, 6, 12], carry=[0.4, 0.55, 0.7],
                pace_ridge=[6], pace_carry=[0.7])
    if args.quick:
        grid = dict(half_life_days=[90], ridge=[6], carry=[0.4, 0.7], pace_ridge=[6], pace_carry=[0.7])
    # 2024 has no prior of its own; it only seeds 2025's.
    seed = backtest.season_end(g24, backtest.prior_from(None, Params()), Params(), const)
    best = None
    for combo in itertools.product(*grid.values()):
        p = Params(**dict(zip(grid, combo)))
        rows = backtest.walk_forward(g25, seed, p, const)
        s = backtest.summarize(rows)
        score = s["margin_mae"] + 0.5 * s["total_mae"]
        print(f"{p}  margin {s['margin_mae']:.3f}  total {s['total_mae']:.3f}  brier {s['brier']:.4f}")
        if best is None or score < best[0]:
            best = (score, p)
    p = best[1]
    print("chosen:", p)

    def tracker(season, games):
        return availability.Tracker(games, availability.lines(ingest.load_players(season)),
                                    availability.lines(ingest.load_players(season - 1))).prepare()

    tune_rows = backtest.walk_forward(g25, seed, p, const, tracker(2025, g25))
    scale = backtest.fit_avail_scale(tune_rows)
    print(f"availability scale (fit on 2025): {scale:.2f} points/100 per unit")
    s25 = backtest.summarize(backtest.with_avail(tune_rows, scale, 13.5))
    was = backtest.summarize(tune_rows)["margin_mae"]
    print(f"2025 with availability: margin {s25['margin_mae']:.3f} (was {was:.3f})")
    end25 = backtest.season_end(g25, backtest.prior_from(seed, p), p, const)
    base_rows = backtest.walk_forward(g26, end25, p, const, tracker(2026, g26))
    print("holdout WITHOUT availability:", json.dumps({k: v for k, v in backtest.summarize(base_rows).items()
                                                       if not isinstance(v, dict)}, default=str))
    rows = backtest.with_avail(base_rows, scale, 14.0)
    full = backtest.summarize(rows, "2025-26 holdout")
    sig = Constants(const.home_eff, const.b2b_off, const.b2b_def, full["margin_sd"], full["total_sd"])
    early = backtest.summarize([r for r in rows if r.day_index < 21], "first three weeks")
    late = backtest.summarize([r for r in rows if r.day_index >= 21], "after three weeks")
    print(json.dumps({"params": p.__dict__, "constants": sig.__dict__, "avail_scale": scale,
                      "holdout": full, "early": early, "late": late}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
