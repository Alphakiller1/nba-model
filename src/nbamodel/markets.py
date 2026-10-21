"""Historical and live market lines: open and close, every pregame book ESPN carries.

ESPN's core odds API keeps, per game, each book's opening and closing spread,
total and moneyline back to 2019-20. "Live Odds" books are in-game prices and
are excluded. Each game is reduced to a consensus (median across books) open
and close, plus the count of books, in ``data/markets/{season}.csv``.

Sign: ``spread_*`` is the HOME handicap (home -4.5 = home favoured by 4.5);
the expected home margin is its negative.
"""
from __future__ import annotations

import csv
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from . import espn, ingest

CORE = "https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba/events/{e}/competitions/{e}/odds"
DATA = Path(__file__).resolve().parents[2] / "data" / "markets"
FIELDS = ["event_id", "books", "spread_open", "spread_close", "total_open", "total_close",
          "home_ml_open", "home_ml_close", "away_ml_open", "away_ml_close"]


def _num(v: Any) -> float | None:
    if v is None:
        return None
    s = str(v).strip().lower().replace("o", "").replace("u", "").replace("+", "")
    if s in ("", "even", "ev", "pk", "pick"):
        return 0.0 if s in ("pk", "pick") else (100.0 if s in ("even", "ev") else None)
    try:
        return float(s)
    except ValueError:
        return None


def _american(node: Any) -> float | None:
    if not isinstance(node, dict):
        return None
    return _num(node.get("american") or node.get("alternateDisplayValue"))


def parse_book(item: dict[str, Any]) -> dict[str, float | None]:
    home = item.get("homeTeamOdds") or {}
    away = item.get("awayTeamOdds") or {}
    out: dict[str, float | None] = {}
    for when in ("open", "close"):
        h, a = home.get(when) or {}, away.get(when) or {}
        out[f"spread_{when}"] = _american(h.get("pointSpread"))
        tot = (item.get(when) or {}).get("total")
        out[f"total_{when}"] = _american(tot)
        out[f"home_ml_{when}"] = _american(h.get("moneyLine"))
        out[f"away_ml_{when}"] = _american(a.get("moneyLine"))
    # Some books carry only current/closing values at the top level.
    if out["spread_close"] is None and item.get("spread") is not None:
        out["spread_close"] = _num(item.get("spread"))
    if out["total_close"] is None and item.get("overUnder") is not None:
        out["total_close"] = _num(item.get("overUnder"))
    if out["home_ml_close"] is None:
        out["home_ml_close"] = _num(home.get("moneyLine"))
    if out["away_ml_close"] is None:
        out["away_ml_close"] = _num(away.get("moneyLine"))
    return out


def consensus(items: list[dict[str, Any]]) -> dict[str, Any]:
    books = [parse_book(it) for it in items
             if "live" not in str((it.get("provider") or {}).get("name", "")).lower()]
    out: dict[str, Any] = {"books": len(books)}
    for k in FIELDS[2:]:
        vals = [b[k] for b in books if b.get(k) is not None]
        if k.startswith("spread"):
            vals = [v for v in vals if abs(v) <= 30]
        if k.startswith("total"):
            vals = [v for v in vals if 170 <= v <= 290]
        out[k] = round(statistics.median(vals), 2) if vals else ""
    return out


def fetch_game(event_id: str) -> dict[str, Any] | None:
    try:
        data = espn.get(CORE.format(e=event_id) + "?limit=50")
    except espn.SourceError:
        return None
    return {"event_id": event_id, **consensus(data.get("items") or [])}


def path(season: int) -> Path:
    return DATA / f"{season}.csv"


def load(season: int) -> dict[str, dict[str, Any]]:
    p = path(season)
    if not p.exists():
        return {}
    out = {}
    with p.open(newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            out[r["event_id"]] = {k: (float(v) if v not in ("", None) and k != "event_id" else v)
                                  for k, v in r.items()}
    return out


def refresh(season: int, workers: int = 10, log=print) -> None:
    have = load(season)
    games = ingest.load_games(season)
    todo = [g.event_id for g in games if g.event_id not in have]
    log(f"markets {season}: {len(games)} games, {len(have)} cached, {len(todo)} to fetch")
    rows = {k: {f: ("" if v is None else v) for f, v in r.items()} for k, r in have.items()}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for i, row in enumerate(pool.map(fetch_game, todo), 1):
            if row:
                rows[row["event_id"]] = row
            if i % 300 == 0:
                log(f"  {season}: {i}/{len(todo)}")
    DATA.mkdir(parents=True, exist_ok=True)
    with path(season).open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        for r in sorted(rows.values(), key=lambda r: r["event_id"]):
            w.writerow(r)
    priced = sum(1 for r in rows.values() if r.get("spread_close") not in ("", None))
    log(f"markets {season}: {len(rows)} rows, {priced} with a closing spread")


if __name__ == "__main__":
    for s in sys.argv[1:]:
        refresh(int(s), log=lambda m: print(m, flush=True))
