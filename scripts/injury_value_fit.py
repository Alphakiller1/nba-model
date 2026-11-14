"""What a missing NBA player is worth to the spread, read off closing lines.

For every 2025-26 game with a DraftKings close, each club's absent regulars -
players averaging >= REGULAR_MIN minutes over the club's previous ten games
who do not appear in this game's box - and their worth as

    x = (O + D - R) * (average minutes / 48)

with O and D the player's RAPM per 100 (player_board.build at season's end)
and R a replacement level. Then, with a strength per club,

    -close = HFA + s_home - s_away + k * (sum x_home - sum x_away)

R is chosen on held-out error (odd games fit, even games predicted, and the
reverse); k, the points of spread per point of such worth, is fitted with it.
A player's value tonight is then k * (O + D - R) * (expected minutes / 48).

    python scripts/injury_value_fit.py      # writes data/injury_value_fit.json
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nbamodel import availability, ingest, player_board  # noqa: E402

SEASON = 2026
REGULAR_MIN = 12.0
OUT = ROOT / "data" / "injury_value_fit.json"


def main() -> None:
    games = sorted(ingest.load_games(SEASON), key=lambda g: g.date_utc)
    lines = ingest.load_players(SEASON)
    end = datetime.fromisoformat(games[-1].date_utc.replace("Z", "+00:00")).astimezone(timezone.utc)
    pm = player_board.build(SEASON, end)

    def net(pid: str) -> float:
        po, pd = pm.prior.get(pid, (pm.params.rookie_off, pm.params.rookie_def))
        return pm.ratings.off.get(pid, po) + pm.ratings.deff.get(pid, pd)

    box: dict[tuple[str, str], dict[str, float]] = defaultdict(dict)
    gmsc: dict[tuple[str, str], float] = {}
    for ln in lines:
        m = float(ln.get("minutes") or 0)
        if m > 0:
            box[(ln["event_id"], ln["team"])][ln["athlete_id"]] = m
            gmsc[(ln["event_id"], ln["athlete_id"])] = availability.game_score(ln)
    history: dict[str, list[str]] = defaultdict(list)
    rows = []
    for g in games:
        feats = {}
        for team in (g.home, g.away):
            prior = history[team][-10:]
            mins: dict[str, list[float]] = defaultdict(list)
            for eid in prior:
                for pid, m in box.get((eid, team), {}).items():
                    mins[pid].append(m)
            here = box.get((g.event_id, team), {})
            gs: dict[str, list[float]] = defaultdict(list)
            for eid in prior:
                for pid in box.get((eid, team), {}):
                    gs[pid].append(gmsc.get((eid, pid), 0.0))
            missing = [(pid, sum(ms) / len(ms), sum(gs[pid]) / len(gs[pid])) for pid, ms in mins.items()
                       if len(ms) >= 3 and sum(ms) / len(ms) >= REGULAR_MIN and pid not in here]
            feats[team] = missing
            history[team].append(g.event_id)
        if not g.dk_spread or len(history[g.home]) < 4 or len(history[g.away]) < 4:
            continue
        rows.append((g, feats[g.home], feats[g.away]))
    teams = sorted({t for g, _, _ in rows for t in (g.home, g.away)})
    tix = {t: i for i, t in enumerate(teams)}
    y = np.array([-float(g.dk_spread) for g, _, _ in rows])

    def design(R: float | None) -> np.ndarray:
        X = np.zeros((len(rows), len(teams) + (2 if R is not None else 1)))
        for n, (g, h, a) in enumerate(rows):
            X[n, tix[g.home]] += 1
            X[n, tix[g.away]] -= 1
            X[n, len(teams)] = 0 if g.neutral else 1
            if R is not None:
                X[n, len(teams) + 1] = (sum((net(p) - R) * m / 48 for p, m, _ in h)
                                        - sum((net(p) - R) * m / 48 for p, m, _ in a))
        return X

    def solve(X, yy):
        return np.linalg.solve(X.T @ X + 1e-3 * np.eye(X.shape[1]), X.T @ yy)

    def heldout(R):
        X = design(R)
        idx = np.arange(len(rows))
        errs = []
        for k in (0, 1):
            tr, te = idx % 2 == k, idx % 2 != k
            b = solve(X[tr], y[tr])
            errs.append(float(np.sqrt(np.mean((X[te] @ b - y[te]) ** 2))))
        return float(np.mean(errs))

    TERMS = {"role": lambda p, m, gs: m / 48, "rapm": lambda p, m, gs: net(p) * m / 48,
             "gmsc": lambda p, m, gs: gs, "gmsc2": lambda p, m, gs: max(gs, 0.0) ** 2 / 10.0}

    def design2(terms=("role", "rapm")) -> np.ndarray:
        """Separate terms per absent regular: role (minutes/48), RAPM quality
        (net x minutes/48) and box production (game score per game)."""
        X = np.zeros((len(rows), len(teams) + 1 + len(terms)))
        for n, (g, h, a) in enumerate(rows):
            X[n, tix[g.home]] += 1
            X[n, tix[g.away]] -= 1
            X[n, len(teams)] = 0 if g.neutral else 1
            for j, t in enumerate(terms):
                f = TERMS[t]
                X[n, len(teams) + 1 + j] = sum(f(*x) for x in h) - sum(f(*x) for x in a)
        return X

    def heldout_x(X):
        idx = np.arange(len(rows))
        errs = []
        for k in (0, 1):
            tr, te = idx % 2 == k, idx % 2 != k
            errs.append(float(np.sqrt(np.mean((X[te] @ solve(X[tr], y[tr]) - y[te]) ** 2))))
        return float(np.mean(errs))

    base = heldout(None)
    grid = {R: heldout(R) for R in (-12.0, -10.0, -8.0, -6.0, -4.0, -2.0, 0.0)}
    variants = {"+".join(t): heldout_x(design2(t)) for t in
                (("role", "rapm"), ("role", "gmsc"), ("gmsc",), ("role", "rapm", "gmsc"), ("rapm", "gmsc"),
                 ("role", "rapm", "gmsc", "gmsc2"), ("role", "gmsc", "gmsc2"), ("role", "rapm", "gmsc2"),
                 ("role", "gmsc2"))}
    print(json.dumps(variants, indent=1))
    pick = tuple(min(variants, key=variants.get).split("+"))
    X2 = design2(pick)
    b = solve(X2, y)
    rng = np.random.default_rng(5)
    boots = np.array([solve(X2[i], y[i])[len(teams) + 1:] for i in
                      (rng.integers(0, len(rows), len(rows)) for _ in range(200))])
    absent = sum(len(h) + len(a) for _, h, a in rows)
    result = {"season": SEASON, "games": len(rows), "absent_regulars": absent,
              "regular_minutes": REGULAR_MIN,
              "heldout_rmse": {"teams_only": round(base, 4), **{k: round(v, 4) for k, v in variants.items()},
                               **{f"R={k:+.0f}": round(v, 4) for k, v in grid.items()}},
              "terms": list(pick),
              # value of a player tonight = sum over terms of points[term] * term(player)
              "points": {t: round(-float(c), 4) for t, c in zip(pick, b[len(teams) + 1:])},
              "se": {t: round(float(s), 4) for t, s in zip(pick, boots.std(axis=0))},
              "home_court": round(float(b[len(teams)]), 3)}
    OUT.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
