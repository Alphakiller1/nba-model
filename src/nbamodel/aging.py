"""Aging curves: how much a player's box impact changes from one season to the next, by age.

The delta method on Basketball-Reference advanced tables, 2018-19 to the last
completed season: for every player in consecutive seasons, the change in OBPM
and DBPM, weighted by the harmonic mean of his minutes in the two seasons (a
player who barely played in either says little). Ages are the season ages
Basketball-Reference publishes. The raw curve is smoothed with a quadratic
fit, which is the shape the aging literature finds (steep early growth, a
plateau in the mid-to-late twenties, then decline).

``delta(age)`` is the expected change from the season a player is ``age`` to
the next. It is a description of league history; rapm.priors() applies it to
move last season's box score toward this season.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import bbref

MIN_AGE, MAX_AGE = 19, 38


@dataclass(frozen=True)
class Curve:
    off: dict[int, float]   # age -> expected OBPM change to next season
    deff: dict[int, float]  # age -> expected DBPM change
    raw_off: dict[int, float]
    raw_def: dict[int, float]
    pairs: dict[int, int]

    def delta(self, age: float | None) -> tuple[float, float]:
        if age is None:
            return 0.0, 0.0
        a = int(max(MIN_AGE, min(MAX_AGE, round(age))))
        return self.off.get(a, 0.0), self.deff.get(a, 0.0)


def _f(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def fit(first: int = 2019, last: int = 2026) -> Curve:
    by_age_o: dict[int, list[tuple[float, float]]] = {}
    by_age_d: dict[int, list[tuple[float, float]]] = {}
    for year in range(first, last):
        a, b = bbref.load(year), bbref.load(year + 1)
        for key, r in a.items():
            n = b.get(key)
            if not n:
                continue
            age, m1, m2 = _f(r.get("age")), _f(r.get("mp")), _f(n.get("mp"))
            o1, o2, d1, d2 = _f(r.get("obpm")), _f(n.get("obpm")), _f(r.get("dbpm")), _f(n.get("dbpm"))
            if None in (age, m1, m2, o1, o2, d1, d2) or m1 < 250 or m2 < 250:
                continue
            w = 2 * m1 * m2 / (m1 + m2)
            a_i = int(age)
            by_age_o.setdefault(a_i, []).append((o2 - o1, w))
            by_age_d.setdefault(a_i, []).append((d2 - d1, w))

    def raw(table):
        return {a: float(np.average([d for d, _ in v], weights=[w for _, w in v]))
                for a, v in table.items() if len(v) >= 15}

    def smooth(r: dict[int, float], table) -> dict[int, float]:
        ages = np.array(sorted(r))
        vals = np.array([r[a] for a in ages])
        wts = np.array([sum(w for _, w in table[a]) for a in ages])
        coef = np.polyfit(ages, vals, 2, w=np.sqrt(wts))
        fitted = {a: float(np.polyval(coef, a)) for a in range(MIN_AGE, MAX_AGE + 1)}
        # A quadratic turns back up past its minimum; players in their mid-thirties
        # do not start improving, so the decline holds at its steepest from there.
        floor_age = min(fitted, key=fitted.get)
        return {a: round(fitted[min(a, floor_age)], 3) for a in fitted}

    ro, rd = raw(by_age_o), raw(by_age_d)
    return Curve(smooth(ro, by_age_o), smooth(rd, by_age_d), {a: round(v, 3) for a, v in ro.items()},
                 {a: round(v, 3) for a, v in rd.items()}, {a: len(v) for a, v in by_age_o.items()})


_CURVE: Curve | None = None


def curve() -> Curve:
    global _CURVE
    if _CURVE is None:
        _CURVE = fit()
    return _CURVE
