"""What the board is allowed to claim.

The authority level is RESEARCH_ONLY and ``may_bet`` is False. Promotion
requires every gate below; none is met on the 2025-26 holdout
(data/backtest.json, reports/BACKTEST.md). Changing ``LEVEL`` without the
record behind it is the one edit this repo must never take.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

LEVEL = "RESEARCH_ONLY"

# Fitted on 2024-25 (scripts/tune.py, scripts/roster_prior.py).
AVAIL_SCALE = 41.0     # points/100 of net rating per unit of rotation value
ROSTER_SCALE = 40.0    # same units, applied to the season prior for roster turnover

RECORD_PATH = Path(__file__).resolve().parents[2] / "data" / "backtest.json"

GATES = (
    ("beats_close_margin", "Home-margin MAE below the DraftKings close on a full holdout season"),
    ("positive_blend_weight", "Positive weight on the model's disagreement with the close"),
    ("ats_over_breakeven", "3+ point disagreements cover above 52.4% on 300+ games"),
    ("live_record", "One live season of graded boards meeting the same three gates"),
)

EDGE_WITHHELD = "model does not beat the closing line - gap, not edge"
NOTE = ("published_margin is the DraftKings margin when ESPN quotes one, otherwise the model margin. "
        "model_margin is independent; market_gap is a disagreement, not an edge.")


def record() -> dict[str, Any]:
    try:
        return json.loads(RECORD_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _unmet(rec: dict[str, Any]) -> list[str]:
    unmet = []
    if not rec or rec.get("model_margin_mae_priced", 99) >= rec.get("market_margin_mae", 0):
        unmet.append("beats_close_margin")
    if not rec or not rec.get("spread_blend_weight", -1) > 0:
        unmet.append("positive_blend_weight")
    ats = (rec or {}).get("ats_gap_3") or {}
    if not (ats.get("bets", 0) >= 300 and ats.get("hit", 0) > 0.524):
        unmet.append("ats_over_breakeven")
    unmet.append("live_record")
    return unmet


UNMET_GATES = _unmet(record())


def _evidence(rec: dict[str, Any]) -> str:
    if not rec:
        return "no backtest record"
    ats = rec.get("ats_gap_3") or {}
    return (f"2025-26 walk-forward holdout: margin MAE {rec['model_margin_mae_priced']} vs DraftKings close "
            f"{rec['market_margin_mae']} on {rec['priced_games']} priced games; blend weight "
            f"{rec['spread_blend_weight']}; 3+ pt disagreements covered {ats.get('wins')}/{ats.get('bets')}")


EVIDENCE = _evidence(record())
