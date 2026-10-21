"""Early-line signals: where the player model disagrees with the line by enough to matter.

The record behind each tier is the fit-free open-line backtest
(scripts/signals_record.py -> data/signals_backtest.json): the raw gap between
the open-safe player model and the consensus OPENING line, 2023-24 to 2025-26,
implausible open-to-close moves removed. No parameter was fitted to produce a
gap, so the record is out of sample for every season.

Two things the record supports, and one it does not:

* the line tends to move toward the model (closing-line value), which is the
  leading indicator of an edge;
* totals have beaten the open at a rate above break-even;
* nothing beats the CLOSE. A signal read against a line that has already moved
  is worth less, and the board says which line it was read against.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

RECORD = Path(__file__).resolve().parents[2] / "data" / "signals_backtest.json"

TIERS = {
    "total": (("strong", 7.0), ("lean", 3.0)),
    # 4-6 point spread gaps: the line moves toward the model (64%) but the
    # side has not beaten the open (50.5% on 554), so they are not a signal.
    "spread": (("strong", 6.0),),
}


def record() -> dict[str, Any]:
    try:
        return json.loads(RECORD.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def classify(market: str, gap: float | None) -> str | None:
    if gap is None:
        return None
    for tier, threshold in TIERS[market]:
        if abs(gap) >= threshold:
            return tier
    return None


def for_game(model_margin: float | None, market_margin: float | None, model_total: float | None,
             market_total: float | None, home: str, away: str, rec: dict[str, Any] | None = None) -> list[dict]:
    rec = rec if rec is not None else record()
    out = []
    if model_total is not None and market_total is not None:
        gap = model_total - market_total
        tier = classify("total", gap)
        if tier:
            out.append({"market": "total", "side": "over" if gap > 0 else "under", "tier": tier,
                        "gap": round(gap, 1), "line": market_total, "model": round(model_total, 1),
                        "backtest": (rec.get("total") or {}).get(tier)})
    if model_margin is not None and market_margin is not None:
        gap = model_margin - market_margin
        tier = classify("spread", gap)
        if tier:
            side = home if gap > 0 else away
            out.append({"market": "spread", "side": side, "tier": tier, "gap": round(gap, 1),
                        "line": round(-market_margin, 1), "model": round(model_margin, 1),
                        "backtest": (rec.get("spread") or {}).get(tier)})
    return out
