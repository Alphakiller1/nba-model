#!/usr/bin/env python3
"""Walk-forward test of the player prop layer (player_props.py).

1. Dispersion: on 2024-25, with each player's rate as of the day before and
   his actual minutes, variance over mean of each stat given minutes.
2. Test on 2025-26 from December 1 (every club 15+ games in): each player who
   played, projected from games strictly before that date with the board's
   expected-minutes rule, against two baselines - his season-to-date average
   and his last-10 average. MAE per stat, the share of results inside the
   published 10th-90th range (80% is calibrated), and the over probability at
   the model's own line (floor(mean) + 0.5) scored by Brier against 0.25.

The test projects at league-average pace and does not tie points to a team
score; the live layer does both. Players who did not play are excluded (a
prop on a player who sits is void).

    python scripts/backtest_props.py            -> data/props_backtest.json
"""
from __future__ import annotations

import json
import math
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nbamodel import player_props as pp  # noqa: E402

SIMS = 300


def expected_minutes(hist, season):
    cur = [float(r["minutes"]) for _, yr, r in hist if yr == season][-10:]
    if cur:
        return sum(cur) / len(cur)
    prev = [float(r["minutes"]) for _, yr, r in hist if yr == season - 1]
    return 0.9 * sum(prev) / len(prev) if prev else 12.0


def fit_dispersion(season: int) -> dict[str, float]:
    book = pp.RateBook(season)
    num: dict[str, float] = defaultdict(float)
    den: dict[str, float] = defaultdict(float)
    for pid, rows in book.lines.items():
        for i, (d, yr, r) in enumerate(rows):
            if yr != season or i < 10 or i % 2:
                continue
            rt = book.rates(pid, before=d)
            if not rt:
                continue
            m = float(r["minutes"])
            for name, col in pp.STATS:
                mu = rt["rates"][name] * m
                if mu > 0.2:
                    num[name] += (float(r.get(col) or 0) - mu) ** 2
                    den[name] += mu
    return {k: round(num[k] / den[k], 3) for k in num}


def test(season: int, start: str) -> dict:
    book = pp.RateBook(season)
    err = defaultdict(lambda: defaultdict(float))
    n = defaultdict(int)
    inside = defaultdict(int)
    brier = defaultdict(float)
    for pid, rows in book.lines.items():
        for i, (d, yr, r) in enumerate(rows):
            if yr != season or d < start or i % 3:
                continue
            hist = book.history(pid, before=d)
            cur = [t for t in hist if t[1] == season]
            if len(cur) < 5:
                continue
            rt = book.rates(pid, before=d)
            mins = expected_minutes(hist, season)
            sim = pp.simulate(rt["rates"], mins, rt["minutes_sd"], 1.0, sims=SIMS, seed=i)
            for name, col in pp.STATS + (("pra", None),):
                y = (float(r.get("pts") or 0) + float(r.get("reb") or 0) + float(r.get("ast") or 0)
                     if name == "pra" else float(r.get(col) or 0))

                def val(row):
                    if name == "pra":
                        return sum(float(row.get(c) or 0) for c in ("pts", "reb", "ast"))
                    return float(row.get(col) or 0)

                s = sim[name]
                season_avg = sum(val(t[2]) for t in cur) / len(cur)
                last10 = sum(val(t[2]) for t in cur[-10:]) / len(cur[-10:])
                err[name]["model"] += abs(s["mean"] - y)
                err[name]["season_avg"] += abs(season_avg - y)
                err[name]["last10"] += abs(last10 - y)
                n[name] += 1
                inside[name] += s["p10"] <= y <= s["p90"]
                if "pmf" in s:  # threes: over probability at the model line, from the pmf
                    line = math.floor(s["mean"]) + 0.5
                    p_over = sum(v for k, v in s["pmf"].items() if int(k) > line)
                    brier[name] += (p_over - (1.0 if y > line else 0.0)) ** 2
    out = {}
    for name in n:
        out[name] = {
            "n": n[name],
            "mae_model": round(err[name]["model"] / n[name], 3),
            "mae_season_avg": round(err[name]["season_avg"] / n[name], 3),
            "mae_last10": round(err[name]["last10"] / n[name], 3),
            "p10_p90_coverage": round(inside[name] / n[name], 3),
        }
        if brier.get(name):
            out[name]["brier_over_model_line"] = round(brier[name] / n[name], 4)
    return out


def over_calibration(season: int, start: str) -> dict:
    """Over probability at the model line, for the continuous stats, from full draws."""
    book = pp.RateBook(season)
    import random
    res = defaultdict(lambda: [0, 0.0, 0.0])
    for pid, rows in book.lines.items():
        for i, (d, yr, r) in enumerate(rows):
            if yr != season or d < start or i % 6:
                continue
            hist = book.history(pid, before=d)
            if sum(1 for t in hist if t[1] == season) < 5:
                continue
            rt = book.rates(pid, before=d)
            mins = expected_minutes(hist, season)
            rng = random.Random(i)
            for name, col in (("points", "pts"), ("rebounds", "reb"), ("assists", "ast")):
                draws = []
                for _ in range(SIMS):
                    m = min(44.0, max(0.0, rng.gauss(mins, rt["minutes_sd"])))
                    draws.append(pp._nbinom(rng, rt["rates"][name] * m, pp.DISPERSION[name]))
                mean = sum(draws) / len(draws)
                line = math.floor(mean) + 0.5
                p = sum(1 for x in draws if x > line) / len(draws)
                y = 1.0 if float(r.get(col) or 0) > line else 0.0
                rr = res[name]
                rr[0] += 1
                rr[1] += (p - y) ** 2
                rr[2] += y
    return {k: {"n": v[0], "brier": round(v[1] / v[0], 4), "over_hit_rate": round(v[2] / v[0], 3)}
            for k, v in res.items()}


def main() -> int:
    disp = fit_dispersion(2025)
    print("dispersion 2024-25:", disp, flush=True)
    pp.DISPERSION.update({k: max(1.0, v) for k, v in disp.items()})
    result = {"dispersion_fit_2024_25": disp, "test_2025_26": test(2026, "2025-12-01"),
              "over_at_model_line_2025_26": over_calibration(2026, "2025-12-01")}
    (ROOT / "data" / "props_backtest.json").write_text(json.dumps(result, indent=1))
    print(json.dumps(result, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
