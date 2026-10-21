"""Walk-forward evaluation against the DraftKings close.

Every game is projected from ratings fitted only on games that tipped before
that day, with the prior built from the previous season's closing ratings, so
nothing a forecaster could not have known enters a forecast. The market
comparison uses ESPN's stored DraftKings closing quote; games without one are
scored for accuracy but left out of every market statistic.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import mean
from typing import Sequence

import numpy as np

from .ingest import Game
from .ratings import Constants, Params, Ratings, fit, local_day, prior_from, project, rest_flags


@dataclass
class Row:
    event_id: str
    date: str
    home: str
    away: str
    day_index: int          # days since the season's first game
    proj_margin: float
    proj_total: float
    win_prob: float
    actual_margin: int
    actual_total: int
    market_margin: float | None
    market_total: float | None
    possessions: float = 0.0
    avail_signal: float = 0.0   # (home - away) rotation-value change, Game Score/min units


def _utc(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def _num(v: str) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def season_end(games: Sequence[Game], prior: Ratings, params: Params, const: Constants) -> Ratings:
    last = max(_utc(g.date_utc) for g in games) + timedelta(days=1)
    return fit(games, as_of=last, prior=prior, params=params, const=const, rest=rest_flags(games))


def walk_forward(games: Sequence[Game], prev: Ratings | None, params: Params,
                 const: Constants, tracker=None, shift: dict[str, float] | None = None) -> list[Row]:
    games = sorted(games, key=lambda g: g.date_utc)
    rest = rest_flags(games)
    prior = prior_from(prev, params, shift)
    by_day: dict[datetime, list[Game]] = defaultdict(list)
    for g in games:
        by_day[local_day(g.date_utc)].append(g)
    first = min(by_day)
    out: list[Row] = []
    for day in sorted(by_day):
        slate = by_day[day]
        as_of = min(_utc(g.date_utc) for g in slate)
        r = fit(games, as_of=as_of, prior=prior, params=params, const=const, rest=rest)
        for g in slate:
            p = project(r, g.home, g.away, const=const, neutral=bool(g.neutral),
                        home_b2b=rest.get((g.event_id, g.home), 0),
                        away_b2b=rest.get((g.event_id, g.away), 0))
            spread = _num(g.dk_spread)
            signal = 0.0
            if tracker is not None:
                signal = (tracker.adjustment(g.home, g.event_id, None)[0]
                          - tracker.adjustment(g.away, g.event_id, None)[0])
            out.append(Row(g.event_id, g.date_utc, g.home, g.away, (day - first).days,
                           p.margin, p.total, p.win_probability,
                           g.home_pts - g.away_pts, g.home_pts + g.away_pts,
                           None if spread is None else -spread, _num(g.dk_total),
                           p.possessions, signal))
    return out


def fit_avail_scale(rows: list[Row]) -> float:
    """OLS of the margin residual on the availability term (points/100 per unit)."""
    x = np.array([r.possessions / 100.0 * r.avail_signal for r in rows])
    y = np.array([r.actual_margin - r.proj_margin for r in rows])
    return float((x @ y) / (x @ x)) if (x @ x) else 0.0


def with_avail(rows: list[Row], scale: float, sigma: float) -> list[Row]:
    out = []
    for r in rows:
        m = r.proj_margin + scale * r.possessions / 100.0 * r.avail_signal
        out.append(Row(**{**r.__dict__, "proj_margin": m,
                          "win_prob": 0.5 * (1 + math.erf(m / sigma / math.sqrt(2)))}))
    return out


def _ats(rows: list[Row], threshold: float) -> dict[str, float]:
    win = loss = push = 0
    for r in rows:
        if r.market_margin is None:
            continue
        gap = r.proj_margin - r.market_margin
        if abs(gap) < threshold:
            continue
        cover = r.actual_margin - r.market_margin
        if cover == 0:
            push += 1
        elif (cover > 0) == (gap > 0):
            win += 1
        else:
            loss += 1
    n = win + loss
    return {"bets": n, "wins": win, "pushes": push, "hit": (win / n) if n else float("nan")}


def _ou(rows: list[Row], threshold: float) -> dict[str, float]:
    win = loss = 0
    for r in rows:
        if r.market_total is None:
            continue
        gap = r.proj_total - r.market_total
        if abs(gap) < threshold or r.actual_total == r.market_total:
            continue
        if (r.actual_total > r.market_total) == (gap > 0):
            win += 1
        else:
            loss += 1
    n = win + loss
    return {"bets": n, "wins": win, "hit": (win / n) if n else float("nan")}


def _blend_weight(rows: list[Row], attr_model: str, attr_market: str, attr_actual: str) -> float:
    """OLS weight on the model in actual ~ market + w * (model - market)."""
    pairs = [(getattr(r, attr_model) - getattr(r, attr_market), getattr(r, attr_actual) - getattr(r, attr_market))
             for r in rows if getattr(r, attr_market) is not None]
    if len(pairs) < 50:
        return float("nan")
    x = np.array([p[0] for p in pairs])
    y = np.array([p[1] for p in pairs])
    return float((x @ y) / (x @ x)) if (x @ x) else float("nan")


def summarize(rows: list[Row], label: str = "") -> dict[str, object]:
    if not rows:
        return {"label": label, "games": 0}
    priced = [r for r in rows if r.market_margin is not None]
    totals = [r for r in rows if r.market_total is not None]
    brier = mean((r.win_prob - (1 if r.actual_margin > 0 else 0)) ** 2 for r in rows)
    resid = [r.actual_margin - r.proj_margin for r in rows]
    return {
        "label": label,
        "games": len(rows),
        "margin_mae": mean(abs(x) for x in resid),
        "margin_bias": mean(resid),
        "margin_sd": float(np.std(resid)),
        "total_mae": mean(abs(r.actual_total - r.proj_total) for r in rows),
        "total_bias": mean(r.actual_total - r.proj_total for r in rows),
        "total_sd": float(np.std([r.actual_total - r.proj_total for r in rows])),
        "brier": brier,
        "log_loss": mean(-math.log(max(1e-9, r.win_prob if r.actual_margin > 0 else 1 - r.win_prob))
                         for r in rows),
        "winner_hit": mean(1.0 if (r.proj_margin > 0) == (r.actual_margin > 0) else 0.0 for r in rows),
        "priced_games": len(priced),
        "model_margin_mae_priced": mean(abs(r.actual_margin - r.proj_margin) for r in priced) if priced else None,
        "market_margin_mae": mean(abs(r.actual_margin - r.market_margin) for r in priced) if priced else None,
        "model_total_mae_priced": mean(abs(r.actual_total - r.proj_total) for r in totals) if totals else None,
        "market_total_mae": mean(abs(r.actual_total - r.market_total) for r in totals) if totals else None,
        "spread_blend_weight": _blend_weight(priced, "proj_margin", "market_margin", "actual_margin"),
        "total_blend_weight": _blend_weight(totals, "proj_total", "market_total", "actual_total"),
        "ats_gap_1": _ats(priced, 1.0),
        "ats_gap_3": _ats(priced, 3.0),
        "ou_gap_3": _ou(totals, 3.0),
    }
