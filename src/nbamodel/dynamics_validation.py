"""Date-held-out, five-game trajectory checks, separate from score validation.

Team targets do not overlap. Calibration is fitted only before February 1 of
the previous season; all later windows are untouched holdouts. These are
retrospective date splits, not a claim that source vintages existed then.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timezone
from statistics import mean

import numpy as np

from . import dynamics as d


def target_shift(train: list[dict], test: list[dict]) -> float:
    total = 0.0
    for defense in (False, True):
        a, b = d.totals(train, defense), d.totals(test, defense)
        for kind, points, _ in d.SHOTS:
            am, an = d.shot_counts(a, kind)
            bm, bn = d.shot_counts(b, kind)
            if an and bn and a["possessions"]:
                total += (-1 if defense else 1) * 100 * points * (bm / bn - am / an) * an / a["possessions"]
    return total


def summary(rows: list[dict], weight: float) -> dict:
    if not rows:
        return {"windows": 0, "teams": 0, "state": "insufficient_future_games"}
    observed = np.array([r["actual_shooting_shift_pp100"] for r in rows])
    pred = np.array([r["predicted_shooting_shift_pp100"] * weight for r in rows])
    errors = np.abs(pred - observed)
    baseline = np.abs(observed)
    prefix_errors = np.abs(np.array([r["prefix_reversion_pp100"] for r in rows]) - observed)
    team_deltas = defaultdict(list)
    for r, delta in zip(rows, errors - baseline):
        team_deltas[r["team"]].append(float(delta))
    # Cluster on team to retain dependence across a team's windows. Opponent
    # overlap still exists, so this is a descriptive uncertainty interval.
    values = np.array([mean(v) for v in team_deltas.values()])
    rng = np.random.default_rng(6101)
    boot = [float(rng.choice(values, len(values), replace=True).mean()) for _ in range(1000)]
    pace_rows = [r for r in rows if r["actual_pace"] is not None and r["predicted_pace"] is not None]
    directional = [(p, y) for p, y in zip(pred, observed) if abs(p) >= 0.5]
    return {
        "state": "available",
        "windows": len(rows),
        "teams": len(team_deltas),
        "shooting_shift_mae_pp100": float(errors.mean()),
        "no_change_mae_pp100": float(baseline.mean()),
        "prefix_rate_reversion_mae_pp100": float(prefix_errors.mean()),
        "shooting_shift_rmse_pp100": float(np.sqrt(np.square(pred - observed).mean())),
        "no_change_rmse_pp100": float(np.sqrt(np.square(observed).mean())),
        "team_equal_mae_delta_95pct": [float(v) for v in np.quantile(boot, [0.025, 0.975])],
        "directional_windows_abs_prediction_ge_0_5": len(directional),
        "directional_accuracy": mean(int((p > 0) == (y > 0)) for p, y in directional) if directional else None,
        "pace_windows": len(pace_rows),
        "pace_mae": mean(abs(r["predicted_pace"] - r["actual_pace"]) for r in pace_rows) if pace_rows else None,
        "season_mean_pace_mae": mean(abs(r["season_mean_pace"] - r["actual_pace"]) for r in pace_rows)
        if pace_rows
        else None,
        "last5_pace_mae": mean(abs(r["last5_pace"] - r["actual_pace"]) for r in pace_rows) if pace_rows else None,
    }


def evaluate(rows: list[dict], season: int, as_of: str, regulation: float) -> tuple[dict, list[dict]]:
    valid = d.before(rows, as_of)
    groups = defaultdict(list)
    for r in valid:
        groups[(r["season"], r["team"])].append(r)
    previous_year = season - 1
    split = datetime(previous_year, 2, 1, tzinfo=timezone.utc)
    results = []
    # Prefix pools are shared by windows with the same cutoff. No target
    # outcomes or end-of-season aggregates enter a reference distribution.
    references = {}
    for (year, team), seq in sorted(groups.items()):
        if year not in (previous_year, season):
            continue
        for start in range(8, len(seq) - 4, 5):
            test = seq[start : start + 5]
            cutoff = test[0]["date"]
            train = d.before(seq[:start], cutoff)
            if len(train) < 8:
                continue
            key = year, cutoff
            if key not in references:
                old = d.before([r for r in valid if r["season"] == year - 1], cutoff)
                current = d.before([r for r in valid if r["season"] == year], cutoff)
                league = old or current
                priors = defaultdict(list)
                for r in old:
                    priors[r["team"]].append(r)
                references[key] = (league, priors, current, old)
            league, priors, current, old = references[key]
            shooting = d.recent_reversion(train, current, priors, old)
            if shooting.get("state") != "available":
                continue
            pace = d.pace(train, priors[team], league, regulation)
            future_pace = [
                r["possessions"] * regulation / r["duration_minutes"]
                for r in test
                if r.get("duration_minutes") and r["duration_minutes"] >= regulation
            ]
            results.append(
                {
                    "season": year,
                    "team": team,
                    "cutoff": cutoff,
                    "train_through": max(r["available_at"] for r in train),
                    "train_games": len(train),
                    "target_games": 5,
                    "target_ids": "|".join(str(r["game_id"]) for r in test),
                    "target_through": test[-1]["date"],
                    "partition": "calibration" if year == previous_year and d.utc(cutoff) < split else "holdout",
                    "predicted_shooting_shift_pp100": shooting["net_reversion_pp100"],
                    "actual_shooting_shift_pp100": target_shift(train[-5:], test),
                    "prefix_reversion_pp100": target_shift(train[-5:], train[:-5]),
                    "predicted_pace": pace.get("posterior_per_regulation"),
                    "season_mean_pace": pace.get("observed_per_regulation"),
                    "last5_pace": pace.get("recent5_per_regulation"),
                    "actual_pace": mean(future_pace) if len(future_pace) == 5 else None,
                }
            )
    train = [r for r in results if r["partition"] == "calibration"]
    xx = sum(r["predicted_shooting_shift_pp100"] ** 2 for r in train)
    xy = sum(r["predicted_shooting_shift_pp100"] * r["actual_shooting_shift_pp100"] for r in train)
    weight = max(0.0, min(1.0, xy / xx)) if xx else 0.0
    if not math.isfinite(weight):
        raise ValueError("Non-finite calibration")
    report = {
        "schema": "basketball-trajectory-validation/1",
        "as_of": as_of,
        "horizon_team_games": 5,
        "stride": 5,
        "minimum_training_games": 8,
        "calibration_cutoff_exclusive": split.isoformat(),
        "calibrated_shooting_weight": weight,
        "method": (
            "Date-censored inputs; nonoverlapping five-game targets per team; "
            "bounded MSE slope fit on calibration only."
        ),
        "target": (
            "Future five-game shot-making change versus the last five at last-five volumes; net offense minus allowed."
        ),
        "limitations": (
            "Retrospective date splits, not source-vintage replay. No shot-quality, "
            "injury or future-opponent adjustment. Team targets share opponents. "
            "Feature design was refined while reviewing historical results; "
            "independent live validation is still required."
        ),
        "score_forecast_weight": 0.0,
        "calibration": summary(train, weight),
        "holdout": {
            str(y): summary([r for r in results if r["partition"] == "holdout" and r["season"] == y], weight)
            for y in (previous_year, season)
        },
    }
    return report, results
