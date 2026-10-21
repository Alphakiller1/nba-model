"""Possession-based team ratings.

Each final game contributes two observations: each club's points per 100
possessions, explained as league average + its offense + the opponent's
defense + home court + rest. A third regression explains possessions with the
two clubs' pace. Both are weighted least squares with a ridge penalty that
pulls every team toward its PRIOR, not toward zero: the prior is last season's
closing rating times ``carry``. Early in a season the prior carries the
rating; by midseason the games do.

Signs: ``off`` is points per 100 scored above average (higher is better).
``deff`` is points per 100 ALLOWED above average (lower is better). ``pace`` is
possessions per 48 above average.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterable, Sequence

import numpy as np

from . import teams
from .ingest import Game

ABBRS = teams.all_abbrs()
IDX = {a: i for i, a in enumerate(ABBRS)}
N = len(ABBRS)
ET = timezone(timedelta(hours=-5))  # rest is counted in US calendar days


@dataclass(frozen=True)
class Params:
    half_life_days: float = 75.0   # recency weighting inside a season
    ridge: float = 6.0             # games-equivalent pull toward the prior
    carry: float = 0.55            # share of last season's rating carried in
    pace_ridge: float = 6.0
    pace_carry: float = 0.7
    mean_ridge: float = 150.0      # team-games-equivalent pull of the league means toward last season


@dataclass(frozen=True)
class Constants:
    """League-wide terms fitted once on past seasons (see calibrate.py)."""
    home_eff: float          # points/100 added to the home offense, taken from the away offense
    b2b_off: float           # points/100 a club on the second night of a back-to-back scores
    b2b_def: float           # points/100 a club on a back-to-back allows
    margin_sigma: float      # SD of actual - projected home margin
    total_sigma: float


@dataclass
class Ratings:
    as_of: str
    mu_eff: float
    mu_pace: float
    off: dict[str, float]
    deff: dict[str, float]
    pace: dict[str, float]
    games: dict[str, int] = field(default_factory=dict)

    def net(self, abbr: str) -> float:
        return self.off[abbr] - self.deff[abbr]

    def zero_mean(self) -> "Ratings":
        """Ratings re-centred so the league averages zero (priors must not drift)."""
        mo = sum(self.off.values()) / N
        md = sum(self.deff.values()) / N
        mp = sum(self.pace.values()) / N
        # A game's possessions are mu_pace + the mean of two club terms, so a
        # uniform shift of mp in every club term moves each game by mp.
        return Ratings(self.as_of, self.mu_eff + mo + md, self.mu_pace + mp,
                       {k: v - mo for k, v in self.off.items()},
                       {k: v - md for k, v in self.deff.items()},
                       {k: v - mp for k, v in self.pace.items()}, dict(self.games))


def local_day(iso: str) -> datetime:
    d = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return d.astimezone(ET).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)


def rest_flags(games: Sequence[Game]) -> dict[tuple[str, str], int]:
    """(event_id, team) -> 1 when that club also played the previous day."""
    played: dict[str, set] = {}
    for g in games:
        day = local_day(g.date_utc)
        played.setdefault(g.home, set()).add(day)
        played.setdefault(g.away, set()).add(day)
    out: dict[tuple[str, str], int] = {}
    for g in games:
        day = local_day(g.date_utc) - timedelta(days=1)
        for t in (g.home, g.away):
            out[(g.event_id, t)] = 1 if day in played.get(t, ()) else 0
    return out


def prior_from(prev: Ratings | None, p: Params, shift: dict[str, float] | None = None) -> Ratings:
    """Last season's closing ratings, regressed by ``carry``.

    ``shift`` moves a club's prior NET rating (points/100) for roster turnover;
    it is split evenly between offense and defense and re-centred so the
    league prior still averages zero.
    """
    shift = shift or {}
    mean_shift = sum(shift.get(a, 0.0) for a in ABBRS) / N
    half = {a: 0.5 * (shift.get(a, 0.0) - mean_shift) for a in ABBRS}
    if prev is None:
        return Ratings("prior", 113.0, 99.0, {a: half[a] for a in ABBRS}, {a: -half[a] for a in ABBRS},
                       {a: 0.0 for a in ABBRS})
    prev = prev.zero_mean()
    return Ratings("prior", prev.mu_eff, prev.mu_pace,
                   {a: p.carry * prev.off[a] + half[a] for a in ABBRS},
                   {a: p.carry * prev.deff[a] - half[a] for a in ABBRS},
                   {a: p.pace_carry * prev.pace[a] for a in ABBRS})


def fit(games: Iterable[Game], *, as_of: datetime, prior: Ratings, params: Params,
        const: Constants, rest: dict[tuple[str, str], int] | None = None) -> Ratings:
    """Ratings from games strictly before ``as_of`` (UTC)."""
    rows = [g for g in games if datetime.fromisoformat(g.date_utc.replace("Z", "+00:00")) < as_of]
    rest = rest or {}
    count = {a: 0 for a in ABBRS}
    if not rows:
        return Ratings(as_of.isoformat(), prior.mu_eff, prior.mu_pace, dict(prior.off),
                       dict(prior.deff), dict(prior.pace), count)

    # Efficiency: unknowns [mu, off(30), deff(30)]
    k = 1 + 2 * N
    xtwx = np.zeros((k, k))
    xtwy = np.zeros(k)
    pk = 1 + N
    ptwp = np.zeros((pk, pk))
    ptwy = np.zeros(pk)
    for g in rows:
        age = (as_of - datetime.fromisoformat(g.date_utc.replace("Z", "+00:00"))).total_seconds() / 86400
        w = 0.5 ** (age / params.half_life_days)
        poss = g.possessions()
        if poss <= 60:
            continue
        count[g.home] += 1
        count[g.away] += 1
        hca = 0.0 if g.neutral else const.home_eff
        for side, pts, team, opp, sign in (("h", g.home_pts, g.home, g.away, 1),
                                           ("a", g.away_pts, g.away, g.home, -1)):
            y = 100.0 * pts / poss
            y -= sign * hca
            y -= const.b2b_off * rest.get((g.event_id, team), 0)
            y -= const.b2b_def * rest.get((g.event_id, opp), 0)
            x = np.zeros(k)
            x[0] = 1.0
            x[1 + IDX[team]] = 1.0
            x[1 + N + IDX[opp]] = 1.0
            xtwx += w * np.outer(x, x)
            xtwy += w * x * y
        xp = np.zeros(pk)
        xp[0] = 1.0
        xp[1 + IDX[g.home]] += 1.0
        xp[1 + IDX[g.away]] += 1.0
        ptwp += w * np.outer(xp, xp)
        ptwy += w * xp * poss

    # Ridge toward the prior: lambda * (beta - beta0)^2 on every team term. The
    # league means are pulled toward last season's with ``mean_ridge`` games of
    # weight, so one opening night cannot move league scoring (it did: two
    # 220-point finals cut every projected total by six points).
    beta0 = np.zeros(k)
    beta0[0] = prior.mu_eff
    reg = np.zeros(k)
    reg[0] = 2.0 * params.mean_ridge
    for a in ABBRS:
        beta0[1 + IDX[a]] = prior.off[a]
        beta0[1 + N + IDX[a]] = prior.deff[a]
        reg[1 + IDX[a]] = params.ridge
        reg[1 + N + IDX[a]] = params.ridge
    beta = np.linalg.solve(xtwx + np.diag(reg), xtwy + reg * beta0)

    p0 = np.zeros(pk)
    p0[0] = prior.mu_pace
    preg = np.zeros(pk)
    preg[0] = params.mean_ridge
    for a in ABBRS:
        p0[1 + IDX[a]] = prior.pace[a] / 2.0  # a game's pace is the SUM of two club terms
        preg[1 + IDX[a]] = params.pace_ridge
    pb = np.linalg.solve(ptwp + np.diag(preg), ptwy + preg * p0)

    return Ratings(
        as_of.isoformat(), float(beta[0]), float(pb[0]),
        {a: float(beta[1 + IDX[a]]) for a in ABBRS},
        {a: float(beta[1 + N + IDX[a]]) for a in ABBRS},
        {a: float(2.0 * pb[1 + IDX[a]]) for a in ABBRS},
        count,
    ).zero_mean()


@dataclass(frozen=True)
class Projection:
    home: str
    away: str
    possessions: float
    home_pts: float
    away_pts: float
    win_probability: float  # P(home wins)

    @property
    def margin(self) -> float:
        return self.home_pts - self.away_pts

    @property
    def total(self) -> float:
        return self.home_pts + self.away_pts


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def project(r: Ratings, home: str, away: str, *, const: Constants, neutral: bool = False,
            home_b2b: int = 0, away_b2b: int = 0) -> Projection:
    poss = r.mu_pace + 0.5 * (r.pace[home] + r.pace[away])
    hca = 0.0 if neutral else const.home_eff
    eh = (r.mu_eff + r.off[home] + r.deff[away] + hca
          + const.b2b_off * home_b2b + const.b2b_def * away_b2b)
    ea = (r.mu_eff + r.off[away] + r.deff[home] - hca
          + const.b2b_off * away_b2b + const.b2b_def * home_b2b)
    hp, ap = poss * eh / 100.0, poss * ea / 100.0
    return Projection(home, away, poss, hp, ap, norm_cdf((hp - ap) / const.margin_sigma))


# Fitted values (scripts/tune.py). Constants come from the 2023-24 and 2024-25
# seasons; the sigmas are the 2025-26 walk-forward residual SDs. Regenerate
# with the script, never by hand.
PARAMS = Params(half_life_days=45.0, ridge=6.0, carry=0.7, pace_ridge=6.0, pace_carry=0.7)
CONSTANTS = Constants(home_eff=0.9085, b2b_off=-1.5369, b2b_def=1.3331,
                      margin_sigma=14.34, total_sigma=18.94)
