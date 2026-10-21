# NBA Model

NBA **research** model from the Chase Analytics model lab: possession-based team
ratings, rest and availability terms, a nightly board against the DraftKings
line, and a graded record.

> Research software, not betting advice. The board is `RESEARCH_ONLY` and
> `may_bet` is false: on the 2025-26 walk-forward holdout the model does **not**
> beat the closing line (see [reports/BACKTEST.md](reports/BACKTEST.md)).

**Live board:** https://alphakiller1.github.io/nba-model/ (`board.json`,
`slate.json`, `build.json`, `ledger.json`), rebuilt on a schedule through the
season.

## Where this fits in Chase Analytics

- **chase-analytics.com `/nba/`** reads `slate.json`: identity, venue, rest,
  injuries, observed team rates and player lines. It carries no model output;
  `export.PUBLIC_FORBIDDEN` is enforced when it is written.
- **chase-analytics.com Model Center** reads `board.json` through
  `/api/model-center/board?sport=nba`.

## What the model does

1. **Ingest** — every final regular-season game from ESPN's game summary: team
   box score, player lines and DraftKings' closing quote, cached in
   `data/games/{season}_games.csv` and `_players.csv`. A final is fetched once.
2. **Ratings** — weighted ridge regressions refit before every slate: each
   club's points per 100 possessions scored and allowed, and possessions per
   game. Games decay with a 45-day half-life; every club is pulled toward a
   prior of last season's closing rating × 0.7, shifted for roster turnover.
3. **League terms** — home court (+0.91 per 100 to the home offense, the same
   off the road offense) and back-to-back (−1.54 scored, +1.33 allowed per
   100), fitted on 2023-24 and 2024-25 with team-season fixed effects.
4. **Availability** — each player's Game Score per minute, shrunk toward
   replacement. A club is adjusted by the change between tonight's available
   rotation (ESPN injury report: Out/Doubtful) and the rotation behind its
   recent games.
5. **Board** — projected score, total, possessions, home win probability
   (normal, σ = 14.3), the DraftKings margin and total, and the gap.
   `published_margin` is the market's when quoted; `edge_points` is always
   `null` with a withheld reason.

## Quick start

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\nba-model refresh            # cache new finals
.\.venv\Scripts\nba-model build --out _site  # board, slate, build, ledger, index.html
.\.venv\Scripts\nba-model status             # authority + record
python scripts\tune.py                       # hyperparameters on 2024-25, score 2025-26
python scripts\roster_prior.py               # roster-turnover prior experiment
python scripts\backtest_report.py            # writes data/backtest.json + reports/BACKTEST.md
```

## Verify

```powershell
.\.venv\Scripts\ruff check src tests scripts
.\.venv\Scripts\python -m pytest -q
```

The suite is offline (ESPN fixtures under `tests/fixtures`). CI also builds the
live board once, because a broken source is a real deploy failure.

## License

All rights reserved — published for transparency and research review.
