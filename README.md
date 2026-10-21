# NBA Model

NBA research model from the Chase Analytics model lab: player-impact ratings
(Bayesian RAPM on lineup stints with a Box Plus/Minus prior), schedule context,
early-line signals against the DraftKings line, and a graded record of each.

> Research software, not betting advice. The board is `RESEARCH_ONLY` and
> `may_bet` is false. Against the **closing** line the model has no edge. Against
> the **opening** line its total signals have beaten break-even in each of three
> seasons and the line moves toward it 61–72% of the time — see
> [reports/BACKTEST.md](reports/BACKTEST.md). Promotion waits on a live record.

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

1. **Ingest** — every final since 2019-20 from ESPN's game summary: box score,
   player lines and **lineup stints** from the play-by-play (`bulk.py`,
   `stints.py`); every pregame book's open and close from ESPN's core odds API
   (`markets.py`); Basketball-Reference advanced tables (`bbref.py`).
2. **Player ratings** — offensive and defensive RAPM on stints, each player
   pulled toward his prior-season Box Plus/Minus (`rapm.py`).
3. **Tonight** — current roster minus ESPN's Out/Doubtful list, at each
   player's recent minutes, times his ratings; pace from the possession team
   model (`player_board.py`, `ratings.py`).
4. **Signals** — gaps to the current DraftKings line big enough to have beaten
   the opening line in the backtest, each with its record (`signals.py`).
5. **Ledger** — the line each signal fired on and the closing line, so live
   closing-line value is measured (`ledger.py`).

## Quick start

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\nba-model refresh            # cache new finals
.\.venv\Scripts\nba-model build --out _site  # board, slate, build, ledger, index.html
.\.venv\Scripts\nba-model status             # authority + record
python -m nbamodel.bulk 2020 2021            # stints + box scores for past seasons
python -m nbamodel.markets 2024 2025         # consensus open/close lines
python -m nbamodel.dataset 2021 2022         # walk-forward feature rows
python scripts\signals_record.py             # data/signals_backtest.json (open-line record)
python scripts\open_edge.py                  # per-season open-line test
python scripts\tune_rapm.py --season 2024    # RAPM settings sweep
python scripts\backtest_report.py            # team model vs the close (reports/TEAM_MODEL_CLOSE.md)
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
