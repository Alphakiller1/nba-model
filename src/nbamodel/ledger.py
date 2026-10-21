"""The live record: every board game's last pregame snapshot, graded at the final.

A game's projection is overwritten by each build until it tips; after that it
is frozen and graded from the box-score cache. The ledger travels with the
published site (``ledger.json``) and each deploy recovers it from there, so an
evicted runner cache never erases the record.
"""
from __future__ import annotations

import json
import urllib.request
from pathlib import Path
from statistics import mean
from typing import Any

from .ingest import Game

PUBLISHED = "https://alphakiller1.github.io/nba-model/ledger.json"
KEEP = ("model_margin", "projected_total", "win_probability", "market_margin", "market_total",
        "published_margin")


def recover(path: Path, url: str = PUBLISHED) -> list[dict[str, Any]]:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8")).get("games", [])
        except ValueError:
            pass
    try:
        with urllib.request.urlopen(url, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8")).get("games", [])
    except Exception:
        return []


def update(rows: list[dict[str, Any]], board: dict[str, Any]) -> list[dict[str, Any]]:
    by_id = {r["event_id"]: r for r in rows}
    for g in board.get("games", []):
        if g.get("game_state") != "scheduled":
            continue
        row = by_id.setdefault(g["id"], {"event_id": g["id"], "away": g["away"], "home": g["home"],
                                         "tip_utc": g["kickoff_utc"], "first_seen_utc": board["generated_at_utc"]})
        if row.get("final"):
            continue
        row.update({k: g.get(k) for k in KEEP})
        row["snapshot_utc"] = board["generated_at_utc"]
    return sorted(by_id.values(), key=lambda r: (r.get("tip_utc", ""), r["event_id"]))


def grade(rows: list[dict[str, Any]], finals: list[Game]) -> list[dict[str, Any]]:
    done = {g.event_id: g for g in finals}
    for r in rows:
        g = done.get(r["event_id"])
        if g and not r.get("final"):
            r["final"] = {"home_pts": g.home_pts, "away_pts": g.away_pts}
    return rows


def summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    graded = [r for r in rows if r.get("final") and r.get("model_margin") is not None]
    if not graded:
        return {"graded": 0}

    def margin(r: dict[str, Any]) -> int:
        return r["final"]["home_pts"] - r["final"]["away_pts"]

    priced = [r for r in graded if r.get("market_margin") is not None]
    ats_w = ats_l = 0
    for r in priced:
        gap = r["model_margin"] - r["market_margin"]
        cover = margin(r) - r["market_margin"]
        if abs(gap) >= 3 and cover != 0:
            if (gap > 0) == (cover > 0):
                ats_w += 1
            else:
                ats_l += 1
    return {
        "graded": len(graded),
        "margin_mae": round(mean(abs(margin(r) - r["model_margin"]) for r in graded), 2),
        "brier": round(mean((r["win_probability"] - (1 if margin(r) > 0 else 0)) ** 2 for r in graded), 4),
        "priced": len(priced),
        "market_margin_mae": round(mean(abs(margin(r) - r["market_margin"]) for r in priced), 2) if priced else None,
        "model_margin_mae_priced": round(mean(abs(margin(r) - r["model_margin"]) for r in priced), 2)
        if priced else None,
        "ats_gap_3": {"wins": ats_w, "bets": ats_w + ats_l},
    }


def write(path: Path, rows: list[dict[str, Any]], generated: str) -> dict[str, Any]:
    payload = {"schema": "nba-model/ledger/1", "generated_at_utc": generated, "summary": summary(rows),
               "games": rows}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")
    return payload
