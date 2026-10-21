#!/usr/bin/env python3
"""Score the shipped configuration on the 2025-26 holdout and write the record.

Writes ``data/backtest.json`` (read by the board's authority block) and
``reports/BACKTEST.md``. Everything shipped - parameters, league constants,
the availability scale and the roster-shift scale - was chosen on 2023-24
and 2024-25; 2025-26 is scored once here.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nbamodel import authority, backtest, ingest  # noqa: E402
from nbamodel import availability as av  # noqa: E402
from nbamodel.ratings import CONSTANTS, PARAMS  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from roster_prior import roster_diff  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    g24, g25, g26 = (ingest.load_games(s) for s in (2024, 2025, 2026))
    seed = backtest.season_end(g24, backtest.prior_from(None, PARAMS), PARAMS, CONSTANTS)
    end25 = backtest.season_end(g25, backtest.prior_from(seed, PARAMS), PARAMS, CONSTANTS)
    tracker = av.Tracker(g26, av.lines(ingest.load_players(2026)), av.lines(ingest.load_players(2025))).prepare()
    shift = {t: authority.ROSTER_SCALE * v for t, v in roster_diff(2026).items()}
    rows = backtest.walk_forward(g26, end25, PARAMS, CONSTANTS, tracker, shift)
    rows = backtest.with_avail(rows, authority.AVAIL_SCALE, CONSTANTS.margin_sigma)
    full = backtest.summarize(rows, "2025-26 walk-forward holdout")
    early = backtest.summarize([r for r in rows if r.day_index < 21], "first three weeks")
    rec = {
        "season": "2025-26", "method": "walk-forward; ratings refit before every game day",
        "games": full["games"], "priced_games": full["priced_games"],
        "margin_mae": round(full["margin_mae"], 3), "total_mae": round(full["total_mae"], 3),
        "brier": round(full["brier"], 4), "log_loss": round(full["log_loss"], 4),
        "winner_hit": round(full["winner_hit"], 4),
        "model_margin_mae_priced": round(full["model_margin_mae_priced"], 3),
        "market_margin_mae": round(full["market_margin_mae"], 3),
        "model_total_mae_priced": round(full["model_total_mae_priced"], 3),
        "market_total_mae": round(full["market_total_mae"], 3),
        "spread_blend_weight": round(full["spread_blend_weight"], 3),
        "total_blend_weight": round(full["total_blend_weight"], 3),
        "ats_gap_3": full["ats_gap_3"], "ats_gap_1": full["ats_gap_1"], "ou_gap_3": full["ou_gap_3"],
        "early_margin_mae": round(early["margin_mae"], 3), "early_games": early["games"],
        "margin_sd": round(full["margin_sd"], 2), "total_sd": round(full["total_sd"], 2),
    }
    (ROOT / "data").mkdir(exist_ok=True)
    (ROOT / "data" / "backtest.json").write_text(json.dumps(rec, indent=1) + "\n", encoding="utf-8")
    ats, ou = rec["ats_gap_3"], rec["ou_gap_3"]
    md = f"""# NBA model backtest - 2025-26 holdout

Walk-forward: before every game day the ratings are refit on the games already
played, with the prior built from 2024-25's closing ratings. Parameters, league
constants, the availability scale ({authority.AVAIL_SCALE}) and the roster-shift
scale ({authority.ROSTER_SCALE}) were all chosen on 2023-24 and 2024-25.

| Measure | Model | DraftKings close |
|---|---|---|
| Home-margin MAE, all {rec['games']} games | {rec['margin_mae']} | - |
| Home-margin MAE, {rec['priced_games']} priced games | {rec['model_margin_mae_priced']} | {rec['market_margin_mae']} |
| Total MAE, priced games | {rec['model_total_mae_priced']} | {rec['market_total_mae']} |
| Brier (home win) | {rec['brier']} | - |
| Winner hit rate | {rec['winner_hit']:.1%} | - |

**Against the market the model has no signal.** Regressing the result on the
close plus the model's disagreement gives a weight of {rec['spread_blend_weight']}
on the spread disagreement and {rec['total_blend_weight']} on the total. Where
the model and the close differ by 3+ points, the model's side covered
{ats['wins']} of {ats['bets']} ({ats['hit']:.1%}); on totals {ou['wins']} of
{ou['bets']} ({ou['hit']:.1%}). Break-even at -110 is 52.4%.

The board is therefore RESEARCH_ONLY: the published margin is the market's when
one is quoted, the model margin is shown beside it as a gap, and no edge is
published.

Caveats: ESPN stores no closing quote for the first three weeks of 2025-26, so
the market comparison covers games from November on. Backtest availability is
read from the box score (who played), which is cleaner than a live injury
report. The roster-shift backtest treats each club's first-ten-game players as
its opening roster.
"""
    (ROOT / "reports").mkdir(exist_ok=True)
    (ROOT / "reports" / "TEAM_MODEL_CLOSE.md").write_text(md, encoding="utf-8")
    print(json.dumps(rec, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
