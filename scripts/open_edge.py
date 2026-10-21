#!/usr/bin/env python3
"""Does the open-safe model beat the OPENING line? Per season, robust to bad opens.

Training uses earlier seasons' relationship between the open-safe model and the
CLOSING line (2020-21 to 2022-23 carry no opening lines), so 2023-24 - the
season with an 11-book consensus open - can be a test season. Each test
season is scored twice: on every game, and with implausible open-to-close
moves removed (spread > 5, total > 8), so one bad book's openers cannot carry
the result.

    python scripts/open_edge.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nbamodel import stack  # noqa: E402

SPECS = {
    "spread": stack.Spec("spread_open", "open", "margin", ("rapm_margin_open",)),
    "spread_both": stack.Spec("spread_open_both", "open", "margin", ("team_margin", "rapm_margin_open")),
    "total": stack.Spec("total_open", "open", "total", ("team_total", "rapm_total_open")),
}
MAX_MOVE = {"margin": 5.0, "total": 8.0}


def as_close(spec: stack.Spec) -> stack.Spec:
    return stack.Spec(spec.name, "close", spec.target, spec.models, spec.schedule)


def run(seasons=range(2021, 2027), tests=(2024, 2025, 2026), thresholds=(1.0, 2.0, 3.0), rng_seed=7) -> dict:
    data = {s: stack.load([s]) for s in seasons}
    rng = np.random.default_rng(rng_seed)
    out: dict = {}
    for key, spec in SPECS.items():
        pooled: dict[str, dict] = {}
        for test in tests:
            train = [r for s in seasons if s < test for r in data[s]]
            Xtr, ytr, _ = stack.design(train, as_close(spec))
            beta = stack.fit(Xtr, ytr)
            Xte, yte, rte = stack.design(data[test], spec)
            gap = Xte @ beta
            close = np.array([stack.market_value(r, as_close(spec)) for r in rte], dtype=float)
            open_ = np.array([stack.market_value(r, spec) for r in rte], dtype=float)
            move = close - open_
            for clean in (False, True):
                ok = np.abs(move) <= MAX_MOVE[spec.target] if clean else np.ones(len(move), bool)
                for t in thresholds:
                    sel = ok & (np.abs(gap) >= t) & (yte != 0)
                    win = ((yte > 0) == (gap > 0))[sel]
                    moved = sel & (move != 0)
                    clv = ((move > 0) == (gap > 0))[moved]
                    tag = f"{'clean' if clean else 'all'} gap>={t:g}"
                    rec = {"bets": int(sel.sum()), "hit": round(float(win.mean()), 4) if sel.any() else None,
                           "clv_toward": round(float(clv.mean()), 4) if moved.any() else None,
                           "avg_move_toward": round(float(np.mean(np.sign(gap[sel]) * move[sel])), 2)
                           if sel.any() else None}
                    out.setdefault(key, {}).setdefault(str(test), {})[tag] = rec
                    p = pooled.setdefault(tag, {"w": [], "c": []})
                    p["w"].append(win)
                    p["c"].append(clv)
        for tag, p in pooled.items():
            w = np.concatenate(p["w"])
            c = np.concatenate(p["c"])
            boot = [rng.choice(w, len(w)).mean() for _ in range(2000)] if len(w) else [np.nan]
            out[key].setdefault("pooled", {})[tag] = {
                "bets": int(len(w)), "hit": round(float(w.mean()), 4) if len(w) else None,
                "hit_90ci": [round(float(np.quantile(boot, 0.05)), 4), round(float(np.quantile(boot, 0.95)), 4)],
                "clv_toward": round(float(c.mean()), 4) if len(c) else None}
    return out


if __name__ == "__main__":
    print(json.dumps(run(), indent=1))
