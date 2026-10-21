#!/usr/bin/env python3
"""Tune the RAPM settings on 2023-24's opening lines; confirm on later seasons.

The objective is fit-free: the raw gap between the open-safe model and the
consensus OPENING line, scored on closing-line value (did the line move toward
the model) and ATS/O-U against the open, with implausible moves removed. One
setting moves at a time from the defaults.

    python scripts/tune_rapm.py --season 2024
    python scripts/tune_rapm.py --season 2025 --params '{"ridge": 800}'
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nbamodel import dataset, rapm, stack  # noqa: E402

GRID = {"ridge": [600.0, 3000.0], "bpm_weight": [0.3, 0.8], "half_life_days": [60.0, 300.0],
        "prev_season_weight": [0.15, 0.7]}


def score(path: Path) -> dict:
    out = {}
    with path.open(newline="", encoding="utf-8") as fh:
        rows = [{k: (stack._f(v) if k not in ("event_id", "date", "home", "away") else v) for k, v in r.items()}
                for r in csv.DictReader(fh)]
    for target, col, mx, thr in (("margin", "rapm_margin_open", 5, 3), ("total", "rapm_total_open", 8, 3)):
        W, C = [], []
        for r in rows:
            o = r.get("spread_open") if target == "margin" else r.get("total_open")
            c = r.get("spread_close") if target == "margin" else r.get("total_close")
            if o is None or c is None or r.get(col) is None:
                continue
            mo, mc = (-o, -c) if target == "margin" else (o, c)
            if abs(mc - mo) > mx:
                continue
            g = r[col] - mo
            res = r[target] - mo
            if abs(g) < thr or res == 0:
                continue
            W.append((res > 0) == (g > 0))
            if mc != mo:
                C.append((mc - mo > 0) == (g > 0))
        out[target] = {"bets": len(W), "hit": round(float(np.mean(W)), 4) if W else None,
                       "clv": round(float(np.mean(C)), 4) if C else None}
        errs = [abs(r[target] - r[col]) for r in rows if r.get(col) is not None]
        out[f"{target}_mae"] = round(float(np.mean(errs)), 3)
    return out


def run(season: int, params: rapm.RapmParams) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        dataset.P = params
        dataset.OUT = Path(tmp)
        path = dataset.build(season, log=lambda m: None)
        return score(path)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, default=2024)
    ap.add_argument("--params", default=None, help="JSON overrides; skips the sweep")
    args = ap.parse_args()
    base = rapm.RapmParams()
    if args.params:
        p = replace(base, **json.loads(args.params))
        print(json.dumps({"params": p.__dict__, **run(args.season, p)}), flush=True)
        return 0
    print(json.dumps({"params": "default", **run(args.season, base)}), flush=True)
    for key, values in GRID.items():
        for v in values:
            p = replace(base, **{key: v})
            print(json.dumps({"params": {key: v}, **run(args.season, p)}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
