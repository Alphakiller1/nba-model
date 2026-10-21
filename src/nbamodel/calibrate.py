"""League-wide constants: home court and back-to-back effects.

Fitted on whole past seasons with a fixed effect per team-season for offense
and defense, so the home and rest terms are measured net of who played whom.
They are fitted on the calibration seasons only; the holdout season never
informs them.
"""
from __future__ import annotations

from typing import Sequence

import numpy as np

from .ingest import Game
from .ratings import ABBRS, IDX, N, rest_flags


def fit_constants(seasons: Sequence[Sequence[Game]]) -> dict[str, float]:
    blocks = len(seasons)
    k = 3 + 2 * N * blocks + blocks  # hca, b2b_off, b2b_def, team-season off/def, season mean
    rows, ys = [], []
    for s, games in enumerate(seasons):
        rest = rest_flags(games)
        base = 3 + 2 * N * s
        for g in games:
            poss = g.possessions()
            if poss <= 60:
                continue
            for pts, team, opp, sign in ((g.home_pts, g.home, g.away, 1), (g.away_pts, g.away, g.home, -1)):
                x = np.zeros(k)
                x[0] = 0.0 if g.neutral else sign
                x[1] = rest.get((g.event_id, team), 0)
                x[2] = rest.get((g.event_id, opp), 0)
                x[base + IDX[team]] = 1.0
                x[base + N + IDX[opp]] = 1.0
                x[3 + 2 * N * blocks + s] = 1.0
                rows.append(x)
                ys.append(100.0 * pts / poss)
    X = np.array(rows)
    y = np.array(ys)
    # A whisper of ridge on the team terms only, to pin the sum-to-zero gauge.
    reg = np.zeros(k)
    reg[3:3 + 2 * N * blocks] = 1e-3
    beta = np.linalg.solve(X.T @ X + np.diag(reg), X.T @ y)
    return {"home_eff": float(beta[0]), "b2b_off": float(beta[1]), "b2b_def": float(beta[2]),
            "observations": int(len(y))}


__all__ = ["fit_constants", "ABBRS"]
