# NBA model v2 — does it beat the market?

**Model:** `nba-rapm-2.0` · **Data:** 2019-20 to 2025-26, 8,291 games with a consensus closing line,
3,687 with a consensus opening line · **Regenerate:** `python -m nbamodel.dataset 2021 … 2026`,
`python scripts/signals_record.py`, `python scripts/open_edge.py`

## Short answer

| Against | Result |
|---|---|
| **Closing line** | **No edge.** The player model is ~0.3–0.5 points of margin MAE worse than the close in every season, and a market-anchored blend fitted on earlier seasons adds nothing (MAE 10.424 vs 10.423 on 4,922 out-of-sample games; disagreements cover 50%). |
| **Opening line — totals** | **Edge in every season.** Gaps of 3–7 points: 54.0% on 1,349 bets (90% CI 51.8–56.2%). Gaps of 7+: 56.7% on 404 (52.5–60.9%). Break-even at -110 is 52.4%. |
| **Opening line — spreads** | **Not proven.** Gaps of 6+: 54.1% on 349, positive in all three seasons, but the 90% interval (49.9–58.7%) includes break-even. Gaps of 4–6: 50.5%, so they are not a signal. |
| **Closing-line value** | **Consistent.** Between open and close the line moves toward the model 61–72% of the time, by tier. |

The edge is the one sharp NBA bettors describe: the opening number is beatable, and the close
has priced in the same information.

## What the model is

1. **Lineup stints** from ESPN play-by-play: every stretch with the same ten players, its points and
   possessions (`stints.py`; reconciled to the box score).
2. **Offensive and defensive RAPM** (`rapm.py`): weighted ridge on stints of this season and last
   (last season at 0.35 weight, 120-day half-life), each player pulled toward 0.8 × his prior-season
   OBPM/DBPM from Basketball-Reference, shrunk toward a below-average default by minutes played.
3. **Tonight's club**: expected minutes × player ratings, scaled to 240 minutes; pace from the
   possession team model.
4. **Schedule context** (`context.py`): rest, back-to-backs, 3-in-4s, travel distance, time-zone shift,
   altitude.

The 2025-26 top ten by O+D RAPM (SGA, Wembanyama, Jokić, Leonard, Antetokounmpo, White, Holmgren,
Daniels, Cunningham, Adebayo) matches the public EPM/LEBRON boards.

## How it was tested

* **Walk-forward, no look-ahead.** Player ratings refit weekly on games already played; every
  feature row in `data/features/` is built only from information before the game.
* **Two variants, never mixed.** *Open-safe* uses the club's previous-game rotation, which is what is
  known when lines open. *Close-info* uses tonight's actual rotation and is only ever compared with
  the close. Scoring the close-info variant against the open would fake an edge.
* **Fit-free signals.** A signal is the raw gap between the open-safe model and the opening line;
  nothing is fitted to produce it, so every season is out of sample. The one tuned setting
  (BPM weight 0.55 → 0.8) was chosen on 2023-24 and confirmed on 2024-25 and 2025-26.
* **Bad opens removed.** 2024-25's opens come from one book with a fat tail (75 spreads moved 5+,
  86 totals moved 8+, against 17 and 12 in 2023-24's 11-book consensus). Moves beyond 5 (spread) or
  8 (total) are excluded so one book's stale openers cannot carry the result.

## Records

### Against the opening line (fit-free, 2023-24 to 2025-26)

| Signal | Gap | Bets | Hit | 90% CI | Line moved toward model | 2023-24 / 2024-25 / 2025-26 |
|---|---|---|---|---|---|---|
| Total strong | 7+ | 404 | 56.7% | 52.5–60.9% | 72% | 53.1% / 61.8% / 56.4% |
| Total lean | 3–7 | 1,349 | 54.0% | 51.8–56.2% | 61% | 54.3% / 53.7% / 54.1% |
| Spread strong | 6+ | 349 | 54.1% | 49.9–58.7% | 61% | 53.8% / 54.8% / 53.8% |

### Accuracy (home-margin MAE)

| Season | Team model | Player model, open-safe | Player model, close-info | Consensus close |
|---|---|---|---|---|
| 2020-21 | 11.22 | 11.08 | 10.94 | 10.71 |
| 2021-22 | 11.08 | 11.05 | 10.92 | 10.64 |
| 2022-23 | 10.19 | 10.24 | 10.10 | 9.76 |
| 2023-24 | 10.98 | 10.92 | 10.90 | 10.54 |
| 2024-25 | 10.88 | 10.99 | 10.84 | 10.52 |
| 2025-26 | 11.33 | 11.40 | 11.23 | 10.87 |

## What would change the verdict

* **Live CLV.** The ledger freezes the line each signal fires on and measures the move to the close.
  The live gate (`authority.py`) needs 150+ live signals with the line moving toward the model on
  55%+ and a graded record above break-even before the board is promoted from RESEARCH_ONLY.
* **Signals read late are worth less.** In production a signal compares the model with the *current*
  line. A line that has already moved toward the model leaves less to win; the board records which
  line it read.

## Known limitations

* Opening lines exist only from 2023-24 on (ESPN stores no opens earlier), so the open-line record is
  three seasons.
* Backtest availability for the open-safe variant is the previous game's rotation; live boards use
  ESPN's injury report, which is better information at open but noisier than a box score.
* Player values for rookies and two-way players rest on a flat prior.
