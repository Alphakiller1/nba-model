"""Tonight's games from player ratings: RAPM x expected minutes.

For each club: the current roster (ESPN) minus players listed Out or Doubtful,
each with expected minutes from his last ten appearances this season (or 90%
of last season's average, or a 12-minute default for a player with no NBA
record), top ten kept and scaled to 240. Team offense/defense per 100 is the
minutes-weighted sum of player O and D (rapm.team_effect); pace comes from the
possession team model.

The minutes rule mirrors the backtest's open-safe variant (dataset.py), which
is the variant the published record was measured on.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from . import ingest, rapm

ROTATION = 10


@dataclass
class PlayerModel:
    ratings: rapm.PlayerRatings
    prior: dict[str, tuple[float, float]]
    names: dict[str, str]
    recent: dict[str, list[float]]      # player -> minutes in his last appearances (this season)
    prev_avg: dict[str, float]
    params: rapm.RapmParams


def build(season: int, as_of: datetime, params: rapm.RapmParams | None = None) -> PlayerModel:
    p = params or rapm.RapmParams()
    games = ingest.load_games(season) + ingest.load_games(season - 1)
    dates = {g.event_id: rapm.epoch(g.date_utc) for g in games}
    lines = ingest.load_players(season)
    prev_lines = ingest.load_players(season - 1)
    names = rapm.names_from(prev_lines) | rapm.names_from(lines)
    prior = rapm.priors(season, names, p)
    rows = rapm.load_rows(season, dates) + rapm.load_rows(season - 1, dates)
    ratings = rapm.Design(rows, season, prior, p).fit(as_of.timestamp())
    order = {g.event_id: dates[g.event_id] for g in games}
    recent: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for r in lines:
        m = float(r.get("minutes") or 0)
        if m > 0 and r["event_id"] in order:
            recent[r["athlete_id"]].append((order[r["event_id"]], m))
    tot: dict[str, float] = defaultdict(float)
    n: dict[str, int] = defaultdict(int)
    for r in prev_lines:
        m = float(r.get("minutes") or 0)
        if m > 0:
            tot[r["athlete_id"]] += m
            n[r["athlete_id"]] += 1
    return PlayerModel(ratings, prior, names,
                       {k: [m for _, m in sorted(v)][-10:] for k, v in recent.items()},
                       {k: tot[k] / n[k] for k in tot}, p)


def expected_minutes(pm: PlayerModel, players: set[str]) -> dict[str, float]:
    est = {}
    for pid in players:
        if pm.recent.get(pid):
            est[pid] = sum(pm.recent[pid]) / len(pm.recent[pid])
        elif pid in pm.prev_avg:
            est[pid] = 0.9 * pm.prev_avg[pid]
        else:
            est[pid] = 12.0
    keep = sorted(est, key=lambda k: -est[k])[:ROTATION]
    return {k: est[k] for k in keep}


def project(pm: PlayerModel, home: str, away: str, home_pool: set[str], away_pool: set[str],
            possessions: float, neutral: bool = False) -> dict[str, Any]:
    r = pm.ratings
    mins = {"home": expected_minutes(pm, home_pool), "away": expected_minutes(pm, away_pool)}
    eff = {s: rapm.team_effect(r, mins[s], pm.prior, pm.params) for s in ("home", "away")}
    h = 0.0 if neutral else r.home
    eh = r.mu + h + eff["home"][0] - eff["away"][1]
    ea = r.mu - h + eff["away"][0] - eff["home"][1]

    def rotation(side: str) -> list[dict[str, Any]]:
        out = []
        for pid, m in sorted(mins[side].items(), key=lambda kv: -kv[1]):
            po, pd = pm.prior.get(pid, (pm.params.rookie_off, pm.params.rookie_def))
            out.append({"player_id": pid, "name": pm.names.get(pid, ""), "minutes": round(m, 1),
                        "off": round(r.off.get(pid, po), 2), "def": round(r.deff.get(pid, pd), 2),
                        "possessions": int(r.poss.get(pid, 0))})
        return out

    return {
        "home_pts": possessions * eh / 100.0, "away_pts": possessions * ea / 100.0,
        "margin": possessions * (eh - ea) / 100.0, "total": possessions * (eh + ea) / 100.0,
        "home_off": eff["home"][0], "home_def": eff["home"][1],
        "away_off": eff["away"][0], "away_def": eff["away"][1],
        "home_rotation": rotation("home"), "away_rotation": rotation("away"),
    }
