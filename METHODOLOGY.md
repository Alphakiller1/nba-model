# Methodology (v2, `nba-rapm-2.0`)

## Data

All public, all cached in `data/`:

| Source | What | Where |
|---|---|---|
| ESPN game summary | box score, player lines, play-by-play | `data/games/`, `data/stints/{season}.csv.gz` |
| ESPN core odds API | every pregame book's open and close: spread, total, moneyline | `data/markets/{season}.csv` |
| Basketball-Reference | season advanced tables (OBPM, DBPM, BPM, VORP, usage) | `data/bbref/` |
| ESPN injury report, rosters, scoreboard | tonight | fetched each build |

ESPN's season year is the year a season ends (2026-27 is `2027`). Possessions
are the mean of both sides' `FGA − OREB + TOV + 0.44·FTA`. Market consensus is
the median across books, live-odds books excluded; ESPN stores opening lines
only from 2023-24 on. `spread_*` is the HOME handicap; the expected home margin
is its negative.

## Lineup stints

A stint is a stretch with the same ten players on the floor (`stints.py`).
In-period substitutions come from the play-by-play; the five who start each
later period are inferred (players who act before being subbed in, plus those
subbed out before being subbed in, topped up from the previous period). Points
come from the running score; team rebounds are excluded from offensive
rebounds. A game's stints reconcile to its box score.

## Player ratings (RAPM)

Each stint gives one row per side on offense:
`100 · points / possessions = μ + home(±) + Σ O[offense five] − Σ D[defense five]`.
Weighted least squares, weight = possessions × `0.5 ^ (age/120 days)`, last
season's stints × 0.35, with a ridge of 1,500 possessions pulling every player
toward his **prior**:

* `0.8 × (OBPM, DBPM)` from last season (or the season before), shrunk by
  minutes `m/(m+500)` toward a default of (−1.5, −0.5);
* the default itself for players with under 150 minutes or no record.

Refit weekly in backtests, before every build live.

## Tonight

Current roster minus ESPN's Out/Doubtful list; each player's expected minutes
are his last ten appearances' average (or 90% of last season's, or 12); the top
ten are kept and scaled to 240. Club offense/defense per 100 = Σ (minutes/48) ×
player O / D. Points per 100 for each side = `μ ± home + O_own − D_opp`; times
possessions from the team model.

## Team model (pace and fallback)

Possession team ratings (`ratings.py`): weighted ridge on points per 100 and
possessions per game, 45-day half-life, prior = last season × 0.7 plus a roster
shift; home court +0.91 per 100, back-to-back −1.54 scored / +1.33 allowed. It
supplies pace, appears on the board as `team_margin`/`team_total`, and prices
the slate if rosters or the player fit are unavailable.

## Schedule context

Rest days (capped at 4), back-to-backs, third game in four days, games in the
last seven, travel distance from the previous game's arena, time-zone shift,
altitude (Denver, Utah) — `context.py`. Used in the market-anchored tests
(`stack.py`); none survives as a standalone signal.

## Signals

A signal is `model − current DraftKings line`:

| Market | Tier | Gap |
|---|---|---|
| Total | strong | 7+ points |
| Total | lean | 3–7 |
| Spread | strong | 6+ |

Each tier carries its record from `data/signals_backtest.json`: the fit-free
gap of the open-safe model against the consensus **opening** line, 2023-24 to
2025-26, with moves beyond 5 (spread) / 8 (total) removed. 4–6 point spread
gaps moved the line toward the model but did not beat the open, so they are not
a signal.

## Board semantics

* `model_margin`, `projected_*` — the player model (`forecast_source` says if
  the team model stood in).
* `market_margin` — DraftKings' expected home margin; `published_margin` is
  the market's when quoted.
* `market_gap`, `total_gap` — disagreements.
* `edge_points` — always `null`: nothing beats the close.
* `signals[]` — tier, side, gap, the line it was read against, and the tier's
  record. `signals_record` holds the full table.
* `rotation` — each side's expected minutes with player O/D ratings.

## Authority

`RESEARCH_ONLY`, `may_bet: false` until every gate in `authority.py` passes:

1. strong total signals beat the open — 90% interval above 52.4%, every season (**met**);
2. strong spread signals beat the open, same test (not met);
3. the model is more accurate than the close (not met);
4. 150+ live signals with the line moving toward the model 55%+ and a graded
   record above break-even (pending; measured from `ledger.json`).

## Known limitations

* Three seasons of opening lines.
* The open-safe backtest rotation is the previous game's; live boards use the
  injury report.
* Rookies and two-way players rest on a flat prior.
* A signal read after the line has moved is worth less than one read at the open.
