# Methodology

## Data

ESPN's public NBA site API only. `data/games/{season}_games.csv` holds one row
per final regular-season game (team box score, venue, neutral flag, DraftKings
close); `_players.csv` one row per player line. ESPN's season year is the year
a season ends (2026-27 is `2027`).

Possessions are the mean of both clubs' `FGA − OREB + TOV + 0.44·FTA`, with
team turnovers counted.

## Ratings

For each game and each club: `100 · points / possessions = μ + off[team] +
def[opponent] ± home + rest`. A second regression explains possessions as
`μ_pace + ½(pace[home] + pace[away])`. Both are weighted least squares:

* weight `0.5 ^ (age_days / 45)`;
* ridge of 6 games' weight on every club term toward its **prior**, and of 150
  games' weight on the league means toward last season's;
* prior = last season's closing rating × 0.7 (pace × 0.7), plus a roster shift.

`off` is points per 100 scored above average (higher is better); `def` is
points per 100 **allowed** above average (lower is better). `net = off − def`.

## League constants

Fitted once on 2023-24 + 2024-25 with a fixed effect per team-season:

| Term | Points per 100 |
|---|---|
| Home offense (taken from the road offense) | +0.91 |
| Club on a back-to-back, scored | −1.54 |
| Club on a back-to-back, allowed | +1.33 |

A back-to-back is a game the day (US Eastern) after another.

## Availability and roster turnover

Player value = `(ΣGameScore + 0.20·250) / (Σminutes + 250) − 0.20` per minute,
with last season at half weight; a player with no minutes last season takes the
season before (a lost year is not replacement level).

*Rotation value* = Σ minutes share × value, shares capped at one floor (240
minutes). Tonight's adjustment is `41 × (tonight − recent)` points per 100 on
the club's net, using the last ten games as "recent" and ESPN's injury report
(Out, Doubtful) for tonight. Before a club has three games this season, tonight
is compared with its full roster at last season's usage instead.

The season prior's net moves by `40 × (roster value − last season's value)`.

The availability scale was fitted on 2024-25 by least squares of the margin
residual; the roster scale was chosen on 2024-25, where it was neutral, and
reduced week 1–3 margin error on 2025-26 from 10.61 to 10.49.

## Board semantics

* `model_margin` — the model's expected home margin. Independent of the market.
* `market_margin` — DraftKings' expected home margin (the negative of ESPN's
  home spread).
* `published_margin` — the market margin when quoted, else the model's.
* `market_gap` — `model − market`. A disagreement, **not** an edge.
* `edge_points` — always `null`; `edge_withheld_reason` says why.
* `win_probability` — `Φ(model_margin / 14.3)`, the home side.

## Authority

`RESEARCH_ONLY`, `may_bet: false`. Promotion would require all of: margin MAE
below the close on a full holdout season, a positive blend weight on the model's
disagreement, 3+ point disagreements covering above 52.4% on 300+ games, and a
live season meeting the same. None is met; see `reports/BACKTEST.md`.

## Known limitations

* Backtest availability uses the box score (who played), cleaner than a live
  injury report, so live accuracy should be expected to be worse.
* ESPN stores no close for the first three weeks of 2025-26.
* Game Score is a box-score proxy; defense-first players are undervalued.
* No travel, altitude or schedule-density terms beyond the back-to-back.
