"""Walk-forward feature rows: one per game, built only from what was known before it.

    python -m nbamodel.dataset 2021 2022 ... (ESPN season years)

For each game:

* ``team_*``   - the possession team-ratings model (ratings.py), refit daily.
* ``rapm_*_open``  - player RAPM x expected minutes, with the rotation known the
  day before (players from the club's previous game). This is the information
  a bettor has when the line OPENS, so it is the one compared with the open.
* ``rapm_*_close`` - the same with tonight's actual rotation (who played), the
  information the CLOSING line prices. Never compared with the open.
* schedule context (context.py) and the consensus open/close market.

Rows land in ``data/features/{season}.csv``.
"""
from __future__ import annotations

import csv
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from . import backtest, context, ingest, markets, rapm
from .ratings import CONSTANTS, PARAMS, local_day

OUT = Path(__file__).resolve().parents[2] / "data" / "features"
REFIT_DAYS = 7
P = rapm.RapmParams()


def _minutes_history(season_lines: list[dict[str, str]], order: dict[str, int]):
    """team -> list of (game index, {player: minutes}) in date order."""
    per_game: dict[tuple[str, str], dict[str, float]] = defaultdict(dict)
    for r in season_lines:
        m = float(r.get("minutes") or 0)
        if m > 0:
            per_game[(r["event_id"], r["team"])][r["athlete_id"]] = m
    hist: dict[str, list[tuple[int, str, dict[str, float]]]] = defaultdict(list)
    for (eid, team), mins in per_game.items():
        if eid in order:
            hist[team].append((order[eid], eid, mins))
    for team in hist:
        hist[team].sort()
    return hist


def _prev_avg_minutes(prev_lines: list[dict[str, str]]) -> dict[str, float]:
    tot: dict[str, float] = defaultdict(float)
    n: dict[str, int] = defaultdict(int)
    for r in prev_lines:
        m = float(r.get("minutes") or 0)
        if m > 0:
            tot[r["athlete_id"]] += m
            n[r["athlete_id"]] += 1
    return {p: tot[p] / n[p] for p in tot}


def _expected(team_hist, upto: int, players: set[str], prev_avg: dict[str, float]) -> dict[str, float]:
    recent = [mins for idx, _, mins in team_hist if idx < upto][-10:]
    out = {}
    for pid in players:
        seen = [m[pid] for m in recent if pid in m]
        if seen:
            out[pid] = sum(seen) / len(seen)
        elif pid in prev_avg:
            out[pid] = 0.9 * prev_avg[pid]
        else:
            out[pid] = 12.0
    return out


def build(season: int, log=print) -> Path:
    games = sorted(ingest.load_games(season), key=lambda g: g.date_utc)
    order = {g.event_id: i for i, g in enumerate(games)}
    lines = ingest.load_players(season)
    prev_lines = ingest.load_players(season - 1)
    names = rapm.names_from(prev_lines) | rapm.names_from(lines)
    prior = rapm.priors(season, names, P)
    dates = {g.event_id: rapm.epoch(g.date_utc) for g in games}
    prev_games = ingest.load_games(season - 1)
    dates.update({g.event_id: rapm.epoch(g.date_utc) for g in prev_games})
    rows = rapm.load_rows(season, dates) + rapm.load_rows(season - 1, dates)
    hist = _minutes_history(lines, order)
    prev_avg = _prev_avg_minutes(prev_lines)
    mk = markets.load(season)
    ctx = context.schedule_context(games)

    # Team-ratings walk-forward (prior chain from two seasons back).
    older = ingest.load_games(season - 2)
    seed = backtest.season_end(older, backtest.prior_from(None, PARAMS), PARAMS, CONSTANTS) if older else None
    prev_end = backtest.season_end(prev_games, backtest.prior_from(seed, PARAMS), PARAMS, CONSTANTS) \
        if prev_games else None
    team_rows = {r.event_id: r for r in backtest.walk_forward(games, prev_end, PARAMS, CONSTANTS)}

    design = rapm.Design(rows, season, prior, P)
    fit_cache: dict[datetime, rapm.PlayerRatings] = {}
    first = local_day(games[0].date_utc)
    out_rows = []
    for g in games:
        day = local_day(g.date_utc)
        anchor = first + timedelta(days=((day - first).days // REFIT_DAYS) * REFIT_DAYS)
        if anchor not in fit_cache:
            as_of = rapm.epoch(min(x.date_utc for x in games if local_day(x.date_utc) >= anchor))
            fit_cache[anchor] = design.fit(as_of)
            log(f"  {season} fit as of {anchor.date()}: {len(fit_cache[anchor].off)} players")
        r = fit_cache[anchor]
        tr = team_rows.get(g.event_id)
        poss = tr.possessions if tr else 100.0
        feats = {}
        for variant in ("open", "close"):
            eff = {}
            for team in (g.home, g.away):
                th = hist.get(team, [])
                i = order[g.event_id]
                if variant == "close":
                    players = next((set(m) for idx, eid, m in th if eid == g.event_id), set())
                else:
                    before = [m for idx, _, m in th if idx < i]
                    players = set(before[-1]) if before else \
                        next((set(m) for idx, eid, m in th if eid == g.event_id), set())
                eff[team] = rapm.team_effect(r, _expected(th, i, players, prev_avg), prior, P)
            home_h = 0.0 if g.neutral else r.home
            eh = r.mu + home_h + eff[g.home][0] - eff[g.away][1]
            ea = r.mu - home_h + eff[g.away][0] - eff[g.home][1]
            feats[f"rapm_margin_{variant}"] = round(poss * (eh - ea) / 100.0, 3)
            feats[f"rapm_total_{variant}"] = round(poss * (eh + ea) / 100.0, 3)
            feats[f"rapm_net_home_{variant}"] = round(eff[g.home][0] + eff[g.home][1], 3)
            feats[f"rapm_net_away_{variant}"] = round(eff[g.away][0] + eff[g.away][1], 3)
        m = mk.get(g.event_id, {})
        out_rows.append({
            "event_id": g.event_id, "season": season, "date": g.date_utc, "home": g.home, "away": g.away,
            "margin": g.home_pts - g.away_pts, "total": g.home_pts + g.away_pts,
            "team_margin": round(tr.proj_margin, 3) if tr else "", "team_total": round(tr.proj_total, 3) if tr else "",
            "possessions": round(poss, 2), **feats, **context.features(g, ctx),
            **{k: m.get(k, "") for k in markets.FIELDS[1:]},
        })
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{season}.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(out_rows[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(out_rows)
    log(f"{season}: {len(out_rows)} rows -> {path}")
    return path


if __name__ == "__main__":
    for s in sys.argv[1:]:
        build(int(s), log=lambda m: print(m, flush=True))
