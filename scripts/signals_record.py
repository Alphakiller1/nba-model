#!/usr/bin/env python3
"""Write data/signals_backtest.json: the record behind each signal tier.

Fit-free: gap = open-safe player model - consensus OPENING line, for every
2023-24 to 2025-26 game with an opening line, implausible open-to-close moves
removed (spread > 5, total > 8). Per tier: bets, hit rate against the open,
a bootstrap 90% interval, the share of moved lines that moved toward the
model (CLV), and the per-season split.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nbamodel import signals, stack  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SEASONS = (2024, 2025, 2026)
COLS = {"spread": ("margin", "rapm_margin_open", "spread", 5.0), "total": ("total", "rapm_total_open", "total", 8.0)}


def main() -> int:
    rng = np.random.default_rng(11)
    out: dict = {"method": "fit-free gap vs consensus opening line; moves > 5 (spread) / > 8 (total) removed",
                 "seasons": ["2023-24", "2024-25", "2025-26"], "breakeven": 0.5238}
    for market, (target, col, key, mx) in COLS.items():
        tiers = dict(signals.TIERS[market])
        out[market] = {}
        for tier, threshold in tiers.items():
            upper = min([t for t in tiers.values() if t > threshold], default=None)
            wins, clv, per = [], [], {}
            for s in SEASONS:
                W = []
                for r in stack.load([s]):
                    o, c = r.get(f"{key}_open"), r.get(f"{key}_close")
                    if o is None or c is None or r.get(col) is None:
                        continue
                    mo, mc = (-o, -c) if target == "margin" else (o, c)
                    if abs(mc - mo) > mx:
                        continue
                    g = r[col] - mo
                    res = r[target] - mo
                    # A tier is its band: "lean" excludes gaps that already make "strong".
                    if abs(g) < threshold or (upper is not None and abs(g) >= upper) or res == 0:
                        continue
                    W.append(bool((res > 0) == (g > 0)))
                    if mc != mo:
                        clv.append(bool((mc - mo > 0) == (g > 0)))
                per[str(s)] = {"bets": len(W), "hit": round(float(np.mean(W)), 4) if W else None}
                wins += W
            w = np.array(wins, dtype=float)
            boot = [rng.choice(w, len(w)).mean() for _ in range(4000)] if len(w) else [float("nan")]
            out[market][tier] = {
                "gap_band": [threshold, upper], "bets": int(len(w)), "hit": round(float(w.mean()), 4),
                "hit_90ci": [round(float(np.quantile(boot, 0.05)), 4), round(float(np.quantile(boot, 0.95)), 4)],
                "clv_toward": round(float(np.mean(clv)), 4) if clv else None, "by_season": per,
            }
    (ROOT / "data" / "signals_backtest.json").write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
