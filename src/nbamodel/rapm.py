"""Player impact: Bayesian offensive/defensive RAPM on lineup stints.

Every stint gives two observations, one per side on offense:

    100 * points / possessions = mu + home(+/-) + sum(O[offense five]) - sum(D[defense five])

O is points per 100 possessions a player adds on offense, D points per 100 he
prevents on defense (higher is better for both). The fit is weighted least
squares (weight = possessions x recency) with a ridge pulling each player
toward a PRIOR, not toward zero:

* last season's Box Plus/Minus split (OBPM, DBPM) scaled by ``bpm_weight``,
* a below-average default for players with no box record (rookies, two-ways).

A player with thousands of possessions is read on his lineups; one with fifty
is read on his box score. That is the Bayesian-RAPM / EPM recipe, built only
from public data.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Sequence

import numpy as np
from scipy import sparse
from scipy.sparse.linalg import spsolve

from . import bbref


@dataclass(frozen=True)
class RapmParams:
    ridge: float = 1500.0            # possessions-equivalent pull toward the prior
    bpm_weight: float = 0.8          # share of last season's (O/D)BPM taken as the prior mean (tuned on 2023-24)
    rookie_off: float = -1.5
    rookie_def: float = -0.5
    prev_season_weight: float = 0.35  # last season's stints count this much
    half_life_days: float = 120.0
    aging: bool = True                # move last season's BPM by the league aging curve (aging.py)


@dataclass
class PlayerRatings:
    off: dict[str, float]
    deff: dict[str, float]
    poss: dict[str, float]          # possessions behind each estimate (this + last season)
    mu: float
    home: float

    def total(self, pid: str) -> float:
        return self.off.get(pid, 0.0) + self.deff.get(pid, 0.0)


@dataclass
class StintRow:
    t: float                        # tip time, epoch seconds
    season: int
    home: tuple[str, ...]
    away: tuple[str, ...]
    home_pts: int
    away_pts: int
    poss: float


def load_rows(season: int, game_dates: dict[str, float]) -> list[StintRow]:
    from .bulk import load_stints
    out = []
    for r in load_stints(season):
        t = game_dates.get(r["event_id"])
        if t is None:
            continue
        h = 0.5 * ((int(r["home_fga"]) - int(r["home_oreb"]) + int(r["home_tov"]) + 0.44 * int(r["home_fta"]))
                   + (int(r["away_fga"]) - int(r["away_oreb"]) + int(r["away_tov"]) + 0.44 * int(r["away_fta"])))
        if h <= 0:
            continue
        out.append(StintRow(t, season, tuple(r["home_players"].split("|")), tuple(r["away_players"].split("|")),
                            int(r["home_pts"]), int(r["away_pts"]), h))
    return out


def priors(season: int, names: dict[str, str], p: RapmParams) -> dict[str, tuple[float, float]]:
    """ESPN athlete id -> (O prior, D prior) from the PREVIOUS season's BPM."""
    table = bbref.load(season - 1)
    older = bbref.load(season - 2)
    out = {}
    for pid, name in names.items():
        key = bbref.norm(name)
        row = table.get(key) or older.get(key)
        try:
            mp = float(row["mp"]) if row else 0.0
            ob, db = float(row["obpm"]), float(row["dbpm"])
        except (TypeError, ValueError, KeyError):
            mp, ob, db = 0.0, None, None
        if ob is None or mp < 150:
            out[pid] = (p.rookie_off, p.rookie_def)
            continue
        if p.aging:
            from . import aging
            do, dd = aging.curve().delta(float(row["age"]) if row.get("age") else None)
            ob, db = ob + do, db + dd
        # BPM on few minutes is noise: shrink toward the rookie default by minutes.
        trust = mp / (mp + 500.0)
        out[pid] = (p.bpm_weight * (trust * ob + (1 - trust) * p.rookie_off / p.bpm_weight),
                    p.bpm_weight * (trust * db + (1 - trust) * p.rookie_def / p.bpm_weight))
    return out


class Design:
    """The stint design matrix for one season and the one before, built once.

    Every refit within a season uses the same rows; only the weights change (a
    row tipped after the fit date weighs zero), so a refit is one sparse
    product and one solve rather than a rebuild.
    """

    def __init__(self, rows: Sequence[StintRow], season: int, prior: dict[str, tuple[float, float]],
                 p: RapmParams):
        use = [r for r in rows if r.season in (season, season - 1)]
        self.players = sorted({pid for r in use for pid in r.home + r.away} | set(prior))
        idx = {pid: i for i, pid in enumerate(self.players)}
        self.idx, self.n, self.p, self.season = idx, len(self.players), p, season
        n = self.n
        k = self.k = 2 + 2 * n  # mu, home, O..., D...
        rr, cc, vv, ys, t, poss, cur, pl = [], [], [], [], [], [], [], []
        row = 0
        for r in use:
            for off, dfn, pts, hsign in ((r.home, r.away, r.home_pts, 1.0), (r.away, r.home, r.away_pts, -1.0)):
                rr += [row, row]
                cc += [0, 1]
                vv += [1.0, hsign]
                for pid in off:
                    rr.append(row)
                    cc.append(2 + idx[pid])
                    vv.append(1.0)
                for pid in dfn:
                    rr.append(row)
                    cc.append(2 + n + idx[pid])
                    vv.append(-1.0)
                ys.append(100.0 * pts / r.poss)
                t.append(r.t)
                poss.append(r.poss)
                cur.append(1.0 if r.season == season else p.prev_season_weight)
                pl.append([idx[x] for x in off])
                row += 1
        self.X = sparse.csr_matrix((vv, (rr, cc)), shape=(row, k))
        self.y, self.t = np.array(ys), np.array(t)
        self.poss, self.cur = np.array(poss), np.array(cur)
        self.offense_players = pl
        beta0 = np.zeros(k)
        beta0[0] = 112.0
        for pid in self.players:
            o, d = prior.get(pid, (p.rookie_off, p.rookie_def))
            beta0[2 + idx[pid]] = o
            beta0[2 + n + idx[pid]] = d
        self.beta0 = beta0
        reg = np.full(k, p.ridge)
        reg[0] = reg[1] = 1e-6
        self.reg = reg

    def fit(self, as_of: float) -> PlayerRatings:
        p = self.p
        age = (as_of - self.t) / 86400.0
        w = np.where(self.t < as_of, self.poss * np.power(0.5, age / p.half_life_days) * self.cur, 0.0)
        if not w.any():
            beta = self.beta0
        else:
            Xw = self.X.multiply(w[:, None]).tocsr()
            A = (self.X.T @ Xw) + sparse.diags(self.reg)
            b = self.X.T @ (w * self.y) + self.reg * self.beta0
            beta = spsolve(A.tocsc(), b)
        played = self.t < as_of
        poss: dict[str, float] = {}
        # Each stint appears twice (once per side on offense); count the five on
        # offense in each row, which covers all ten players once per stint.
        for i in np.nonzero(played)[0]:
            for j in self.offense_players[i]:
                pid = self.players[j]
                poss[pid] = poss.get(pid, 0.0) + float(self.poss[i])
        n, idx = self.n, self.idx
        return PlayerRatings({pid: float(beta[2 + idx[pid]]) for pid in self.players},
                             {pid: float(beta[2 + n + idx[pid]]) for pid in self.players},
                             poss, float(beta[0]), float(beta[1]))


def fit(rows: Sequence[StintRow], *, as_of: float, season: int, prior: dict[str, tuple[float, float]],
        p: RapmParams) -> PlayerRatings:
    """One-off fit; walk-forward callers build a Design once and call Design.fit."""
    return Design(rows, season, prior, p).fit(as_of)


def epoch(iso: str) -> float:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def team_effect(r: PlayerRatings, minutes: dict[str, float], prior: dict[str, tuple[float, float]],
                p: RapmParams) -> tuple[float, float]:
    """(offense, defense) per 100 for a rotation given expected minutes per player.

    Minutes are scaled to 240 (five on the floor for 48); each player counts in
    proportion to the share of the game he is expected to be on the floor.
    """
    total = sum(minutes.values())
    if total <= 0:
        return 0.0, 0.0
    scale = 240.0 / total
    o = d = 0.0
    for pid, m in minutes.items():
        f = m * scale / 48.0
        po, pd = prior.get(pid, (p.rookie_off, p.rookie_def))
        o += f * r.off.get(pid, po)
        d += f * r.deff.get(pid, pd)
    return o, d


def names_from(lines: Iterable[dict[str, str]]) -> dict[str, str]:
    return {r["athlete_id"]: r.get("name", "") for r in lines if r.get("athlete_id")}
