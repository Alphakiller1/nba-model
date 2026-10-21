# NBA model backtest - 2025-26 holdout

Walk-forward: before every game day the ratings are refit on the games already
played, with the prior built from 2024-25's closing ratings. Parameters, league
constants, the availability scale (41.0) and the roster-shift
scale (40.0) were all chosen on 2023-24 and 2024-25.

| Measure | Model | DraftKings close |
|---|---|---|
| Home-margin MAE, all 1231 games | 11.17 | - |
| Home-margin MAE, 979 priced games | 11.439 | 11.061 |
| Total MAE, priced games | 15.035 | 14.664 |
| Brier (home win) | 0.2016 | - |
| Winner hit rate | 69.2% | - |

**Against the market the model has no signal.** Regressing the result on the
close plus the model's disagreement gives a weight of -0.206
on the spread disagreement and 0.033 on the total. Where
the model and the close differ by 3+ points, the model's side covered
134 of 289 (46.4%); on totals 237 of
472 (50.2%). Break-even at -110 is 52.4%.

The board is therefore RESEARCH_ONLY: the published margin is the market's when
one is quoted, the model margin is shown beside it as a gap, and no edge is
published.

Caveats: ESPN stores no closing quote for the first three weeks of 2025-26, so
the market comparison covers games from November on. Backtest availability is
read from the box score (who played), which is cleaner than a live injury
report. The roster-shift backtest treats each club's first-ten-game players as
its opening roster.
