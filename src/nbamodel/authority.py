"""What the board is allowed to claim.

The authority level is RESEARCH_ONLY and ``may_bet`` is False until every gate
below is met. The gates are about the MARKET, not about accuracy:

* open-line gates read data/signals_backtest.json (scripts/signals_record.py),
  the fit-free record of the player model against the consensus opening line;
* the closing-line gate reads data/backtest.json (the 2025-26 holdout);
* the live gate reads the published ledger's signal record (ledger.py).

Changing ``LEVEL`` without the record behind it is the one edit this repo must
never take.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

LEVEL = "RESEARCH_ONLY"
BREAKEVEN = 0.5238

# Fitted on 2024-25 (scripts/tune.py, scripts/roster_prior.py).
AVAIL_SCALE = 41.0     # points/100 of net rating per unit of rotation value
ROSTER_SCALE = 40.0    # same units, applied to the season prior for roster turnover

DATA = Path(__file__).resolve().parents[2] / "data"
RECORD_PATH = DATA / "backtest.json"
SIGNALS_PATH = DATA / "signals_backtest.json"

LIVE_MIN_SIGNALS = 150
LIVE_MIN_CLV = 0.55

GATES = (
    ("open_totals_edge", "Strong total signals beat the opening line: 90% interval above 52.4%, every season"),
    ("open_spread_edge", "Strong spread signals beat the opening line: 90% interval above 52.4%, every season"),
    ("beats_close", "Model margin more accurate than the DraftKings close on a holdout season"),
    ("live_clv", f"{LIVE_MIN_SIGNALS}+ live signals, line moved toward the model on {LIVE_MIN_CLV:.0%}+, "
                 "and graded above break-even"),
)

EDGE_WITHHELD = "model does not beat the closing line - gap, not edge"
NOTE = ("published_margin is the DraftKings margin when ESPN quotes one, otherwise the model margin. "
        "model_margin is the player model (RAPM x tonight's rotation); market_gap is a disagreement. "
        "signals[] are early-line flags with their opening-line record; none beats the close.")


def _load(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def record() -> dict[str, Any]:
    return _load(RECORD_PATH)


def signal_record() -> dict[str, Any]:
    return _load(SIGNALS_PATH)


def _tier_passes(tier: dict[str, Any] | None) -> bool:
    if not tier:
        return False
    seasons = [v.get("hit") for v in (tier.get("by_season") or {}).values()]
    return (tier.get("hit_90ci") or [0])[0] > BREAKEVEN and bool(seasons) and all(
        h is not None and h > BREAKEVEN for h in seasons)


def unmet(live: dict[str, Any] | None = None) -> list[str]:
    sig, rec = signal_record(), record()
    out = []
    if not _tier_passes((sig.get("total") or {}).get("strong")):
        out.append("open_totals_edge")
    if not _tier_passes((sig.get("spread") or {}).get("strong")):
        out.append("open_spread_edge")
    if not rec or rec.get("model_margin_mae_priced", 99) >= rec.get("market_margin_mae", 0):
        out.append("beats_close")
    live = live or {}
    if not (live.get("signals", 0) >= LIVE_MIN_SIGNALS and (live.get("clv_toward") or 0) >= LIVE_MIN_CLV
            and (live.get("hit") or 0) > BREAKEVEN):
        out.append("live_clv")
    return out


UNMET_GATES = unmet()


def _evidence() -> str:
    sig, rec = signal_record(), record()
    bits = []
    for market in ("total", "spread"):
        for tier, r in (sig.get(market) or {}).items():
            bits.append(f"{market} {tier} (gap {r['gap_band'][0]:g}+): {r['hit']:.1%} on {r['bets']} vs the open, "
                        f"line moved toward the model {r['clv_toward']:.0%}")
    if rec:
        bits.append(f"vs the close: margin MAE {rec['model_margin_mae_priced']} vs {rec['market_margin_mae']}")
    return "; ".join(bits) or "no backtest record"


EVIDENCE = _evidence()
