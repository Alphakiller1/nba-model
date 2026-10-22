"""Player prop projections: points, rebounds, assists, threes and PRA.

Each player's per-minute rate for a stat is a recency-weighted average of his
games this season and last (last season counts half, and every game decays
with a 40-game half-life), shrunk toward his position's league rate by
SHRINK_MIN minutes of prior. Expected minutes are the board's own rotation
rule (player_board.expected_minutes), so the props and the game projection
read the same rotation.

Tonight's environment enters twice: every stat scales with the game's
projected possessions against the league average, and each club's projected
points are tied to its projected score from the player model (bounded, so a
thin rotation cannot be inflated past what its players do).

The spread is simulated: minutes vary around the expectation by the player's
own recent minutes spread, and the stat given minutes is negative binomial
with the over-dispersion measured on past seasons (DISPERSION, refit by
scripts/backtest_props.py). The published record is that script's walk-
forward test on 2025-26, which never sees the game it projects.
"""
from __future__ import annotations

import math
import random
from collections import defaultdict
from typing import Any

from . import ingest

STATS = (("points", "pts"), ("rebounds", "reb"), ("assists", "ast"), ("threes", "tpm"))
HALF_LIFE = 40.0
PREV_WEIGHT = 0.5
SHRINK_MIN = 40.0
LEAGUE_POSS = 99.5
POINTS_BOUND = (0.85, 1.15)
SIMS = 4000
Q_LEVELS = (0.1, 0.25, 0.5, 0.75, 0.9)
# Half-life, shrinkage and last season's weight were chosen on 2024-25 and
# checked on 2025-26 (scripts/backtest_props.py). Variance over mean of the
# stat given minutes (negative binomial), fit on 2024-25.
DISPERSION = {"points": 2.35, "rebounds": 1.13, "assists": 1.13, "threes": 1.08}


def _pos(p: str) -> str:
    p = (p or "").upper()
    return "C" if "C" in p else ("G" if p.startswith("G") or p in ("PG", "SG") else "F")


class RateBook:
    """Per-minute rates for every player, as of a moment, from box scores."""

    def __init__(self, season: int, before_ts: float | None = None):
        self.season = season
        games = {g.event_id: g.date_utc for g in ingest.load_games(season) + ingest.load_games(season - 1)}
        self.lines: dict[str, list[tuple[str, int, dict]]] = defaultdict(list)
        for yr, rows in ((season, ingest.load_players(season)), (season - 1, ingest.load_players(season - 1))):
            for r in rows:
                d = games.get(r["event_id"])
                if d and float(r.get("minutes") or 0) > 0:
                    self.lines[r["athlete_id"]].append((d, yr, r))
        for v in self.lines.values():
            v.sort(key=lambda t: t[0])
        self.before = before_ts
        self.pos_rate = self._position_rates()

    def history(self, pid: str, before: str | None = None) -> list[tuple[str, int, dict]]:
        rows = self.lines.get(pid, [])
        return [t for t in rows if before is None or t[0] < before]

    def _position_rates(self) -> dict[str, dict[str, float]]:
        tot: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        for rows in self.lines.values():
            for _, yr, r in rows:
                if yr != self.season - 1:
                    continue
                pos = _pos(r.get("position"))
                tot[pos]["min"] += float(r["minutes"])
                for name, col in STATS:
                    tot[pos][name] += float(r.get(col) or 0)
        return {pos: {name: t[name] / t["min"] for name, _ in STATS} for pos, t in tot.items() if t["min"]}

    def rates(self, pid: str, before: str | None = None) -> dict[str, Any] | None:
        hist = self.history(pid, before)
        if not hist:
            return None
        pos = _pos(hist[-1][2].get("position"))
        prior = self.pos_rate.get(pos) or next(iter(self.pos_rate.values()))
        n = len(hist)
        wmin = 0.0
        wsum: dict[str, float] = defaultdict(float)
        mins = []
        for i, (_, yr, r) in enumerate(hist):
            w = 0.5 ** ((n - 1 - i) / HALF_LIFE) * (1.0 if yr == self.season else PREV_WEIGHT)
            m = float(r["minutes"])
            wmin += w * m
            for name, col in STATS:
                wsum[name] += w * float(r.get(col) or 0)
            if yr == self.season:
                mins.append(m)
        out = {name: (wsum[name] + SHRINK_MIN * prior[name]) / (wmin + SHRINK_MIN) for name, _ in STATS}
        recent = mins[-10:] or [float(r["minutes"]) for _, _, r in hist[-10:]]
        mu = sum(recent) / len(recent)
        sd = math.sqrt(sum((m - mu) ** 2 for m in recent) / max(1, len(recent) - 1)) if len(recent) > 1 else 6.0
        return {"rates": out, "position": pos, "games": sum(1 for _, yr, _ in hist if yr == self.season),
                "games_total": n, "minutes_sd": min(8.0, max(2.5, sd)),
                "name": hist[-1][2].get("name", ""), "team": hist[-1][2].get("team", "")}


def _nbinom(rng: random.Random, mean: float, disp: float) -> int:
    """Negative binomial with variance disp*mean (gamma-Poisson); Poisson when disp <= 1."""
    if mean <= 0:
        return 0
    lam = mean
    if disp > 1.0:
        shape = mean / (disp - 1.0)
        lam = rng.gammavariate(shape, mean / shape)
    # Knuth for small means, normal approximation beyond.
    if lam < 30:
        limit, k, p = math.exp(-lam), 0, 1.0
        while True:
            p *= rng.random()
            if p <= limit:
                return k
            k += 1
    return max(0, int(round(rng.gauss(lam, math.sqrt(lam)))))


def _summary(draws: list[int], discrete: bool) -> dict[str, Any]:
    draws = sorted(draws)
    n = len(draws)

    def q(level: float) -> float:
        return float(draws[min(n - 1, int(level * n))])

    out: dict[str, Any] = {"mean": round(sum(draws) / n, 2), "p10": q(0.1), "p50": q(0.5), "p90": q(0.9)}
    if discrete:
        counts: dict[int, int] = defaultdict(int)
        for d in draws:
            counts[d] += 1
        out["pmf"] = {str(k): round(v / n, 4) for k, v in sorted(counts.items()) if v / n >= 0.0005}
    else:
        out["q"] = [q(lv) for lv in Q_LEVELS]
        out["q_levels"] = list(Q_LEVELS)
    return out


def simulate(rates: dict[str, float], minutes: float, minutes_sd: float, pace: float,
             points_scale: float = 1.0, sims: int = SIMS, seed: int = 0) -> dict[str, dict[str, Any]]:
    rng = random.Random(seed)
    draws: dict[str, list[int]] = defaultdict(list)
    for _ in range(sims):
        m = min(44.0, max(0.0, rng.gauss(minutes, minutes_sd)))
        tot = 0
        for name, _ in STATS:
            mean = rates[name] * m * pace * (points_scale if name == "points" else 1.0)
            v = _nbinom(rng, mean, DISPERSION[name])
            draws[name].append(v)
            if name != "threes":
                tot += v
        draws["pra"].append(tot)
    return {name: _summary(d, discrete=name == "threes") for name, d in draws.items()}


def project_game(book: RateBook, game: dict[str, Any], injuries: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Props for every player in a board game's rotation (board game dict with rotation + projections)."""
    rot = game.get("rotation") or {}
    poss = game.get("projected_possessions") or LEAGUE_POSS
    pace = poss / LEAGUE_POSS
    out = []
    for side in ("away", "home"):
        team = game[side]
        players = []
        for r in rot.get(side) or []:
            rt = book.rates(r["player_id"])
            if rt and r.get("minutes", 0) >= 8:
                players.append((r, rt))
        raw_pts = sum(rt["rates"]["points"] * r["minutes"] * pace for r, rt in players)
        target = game.get(f"projected_{side}_score")
        scale = 1.0
        if raw_pts > 0 and target:
            scale = min(POINTS_BOUND[1], max(POINTS_BOUND[0], target / raw_pts))
        for r, rt in players:
            stats = simulate(rt["rates"], r["minutes"], rt["minutes_sd"], pace, scale,
                             seed=int(r["player_id"]) % 100003)
            stats["minutes"] = {"mean": round(r["minutes"], 1)}
            out.append({
                "game_id": game["id"], "game_key": game["key"], "kickoff_utc": game.get("kickoff_utc"),
                "team": team, "opponent": game["home" if side == "away" else "away"], "home": side == "home",
                "player_id": r["player_id"], "player_name": r.get("name") or rt["name"],
                "position": rt["position"], "games": rt["games"], "history_games": rt["games_total"],
                "injury_status": (injuries or {}).get(r["player_id"]),
                "minutes": round(r["minutes"], 1), "points_scale": round(scale, 3), "stats": stats,
            })
    return out
