"""Market-anchored forecasts and the honest test of whether they beat the line.

The question is never "is the model accurate" but "does the model know
something the line does not". So every forecast starts at the market and asks
how far to move off it:

    actual - market_margin = w . (model_margin - market_margin) + b . schedule + e

fitted by least squares on earlier seasons only (expanding window), then
scored on the next season. Two separate tests, never mixed:

* OPEN  - features known when the line opens (yesterday's rotation), against
  the consensus opening line. Scored on ATS vs the open and on closing-line
  value: whether the line then moved toward the model's side.
* CLOSE - features known at tip (tonight's rotation), against the consensus
  close. This is the hard test; a closing line already prices the rotation.

Totals are tested the same way. Break-even at -110 is 52.38%.
"""
from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import numpy as np

FEATURES = Path(__file__).resolve().parents[2] / "data" / "features"
BREAKEVEN = 0.5238
SCHEDULE = ["rest_diff", "b2b_diff", "three_in_four_diff", "travel_diff_1000km", "tz_diff", "games7_diff",
            "altitude"]


def _f(v) -> float | None:
    try:
        x = float(v)
        return None if math.isnan(x) else x
    except (TypeError, ValueError):
        return None


def load(seasons: Sequence[int]) -> list[dict]:
    rows = []
    for s in seasons:
        p = FEATURES / f"{s}.csv"
        if not p.exists():
            continue
        with p.open(newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                rows.append({k: (_f(v) if k not in ("event_id", "date", "home", "away") else v) for k, v in r.items()})
    return rows


@dataclass
class Spec:
    name: str
    line: str                     # "open" or "close"
    target: str                   # "margin" or "total"
    models: tuple[str, ...]       # feature columns giving model forecasts
    schedule: bool = True


def market_value(r: dict, spec: Spec) -> float | None:
    if spec.target == "margin":
        s = r.get(f"spread_{spec.line}")
        return None if s is None else -s
    return r.get(f"total_{spec.line}")


def design(rows: list[dict], spec: Spec) -> tuple[np.ndarray, np.ndarray, list[dict]]:
    X, y, keep = [], [], []
    for r in rows:
        m = market_value(r, spec)
        actual = r.get(spec.target)
        mods = [r.get(c) for c in spec.models]
        if m is None or actual is None or any(v is None for v in mods):
            continue
        feats = [v - m for v in mods]
        if spec.schedule and spec.target == "margin":
            feats += [r.get(c) or 0.0 for c in SCHEDULE]
        X.append(feats)
        y.append(actual - m)
        keep.append(r)
    return np.array(X), np.array(y), keep


def fit(X: np.ndarray, y: np.ndarray, ridge: float = 50.0) -> np.ndarray:
    # No intercept: a forecast that sits on the market predicts a zero residual.
    return np.linalg.solve(X.T @ X + ridge * np.eye(X.shape[1]), X.T @ y)


def score(rows: list[dict], pred_gap: np.ndarray, spec: Spec, thresholds=(0.0, 1.0, 2.0, 3.0)) -> dict:
    """ATS / O-U record of taking the model's side when it differs from the line by >= t."""
    out = {}
    for t in thresholds:
        w = losses = 0
        clv_good = clv_n = 0
        for r, g in zip(rows, pred_gap):
            if abs(g) < max(t, 1e-9):
                continue
            m = market_value(r, spec)
            res = r[spec.target] - m
            if res == 0:
                continue
            if (res > 0) == (g > 0):
                w += 1
            else:
                losses += 1
            if spec.line == "open":
                close = market_value(r, Spec(spec.name, "close", spec.target, ()))
                if close is not None and close != m:
                    clv_n += 1
                    clv_good += 1 if (close - m > 0) == (g > 0) else 0
        n = w + losses
        out[f"gap>={t:g}"] = {"bets": n, "hit": round(w / n, 4) if n else None,
                              "clv_moved": clv_n, "clv_toward": round(clv_good / clv_n, 4) if clv_n else None}
    return out


def evaluate(specs: Sequence[Spec], seasons: Sequence[int], first_test: int) -> dict:
    data = {s: load([s]) for s in seasons}
    report = {}
    for spec in specs:
        preds, test_rows, coefs = [], [], []
        for test in [s for s in seasons if s >= first_test]:
            train = [r for s in seasons if s < test for r in data[s]]
            Xtr, ytr, _ = design(train, spec)
            Xte, yte, rte = design(data[test], spec)
            if len(Xtr) < 300 or not len(Xte):
                continue
            beta = fit(Xtr, ytr)
            coefs.append({"test": test, "train_games": len(Xtr), "beta": [round(b, 3) for b in beta]})
            preds.extend(Xte @ beta)
            test_rows.extend(rte)
        preds = np.array(preds)
        if not len(preds):
            continue
        resid = np.array([r[spec.target] - market_value(r, spec) for r in test_rows])
        mae_mkt = float(np.mean(np.abs(resid)))
        mae_model = float(np.mean(np.abs(resid - preds)))
        report[spec.name] = {
            "test_games": len(test_rows), "mae_market": round(mae_mkt, 3), "mae_anchored": round(mae_model, 3),
            "corr_pred_resid": round(float(np.corrcoef(preds, resid)[0, 1]), 4) if np.std(preds) > 0 else None,
            "record": score(test_rows, preds, spec), "fits": coefs,
        }
    return report


DEFAULT_SPECS = (
    Spec("spread_open_team", "open", "margin", ("team_margin",)),
    Spec("spread_open_rapm", "open", "margin", ("rapm_margin_open",)),
    Spec("spread_open_both", "open", "margin", ("team_margin", "rapm_margin_open")),
    Spec("spread_close_both", "close", "margin", ("team_margin", "rapm_margin_close")),
    Spec("spread_close_rapm", "close", "margin", ("rapm_margin_close",)),
    Spec("total_open_both", "open", "total", ("team_total", "rapm_total_open")),
    Spec("total_close_both", "close", "total", ("team_total", "rapm_total_close")),
)


if __name__ == "__main__":
    print(json.dumps(evaluate(DEFAULT_SPECS, range(2021, 2027), 2023), indent=1))
