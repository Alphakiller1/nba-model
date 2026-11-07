"""Explicit two-season factual matchup evidence; no silent prior-year substitution."""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

from . import coverage, databallr, features, ingest, profiles, teams


def distribution(values) -> dict:
    values = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    if not values:
        return {"mean": None, "std": None, "sample_size": 0}
    mean = sum(values) / len(values)
    return {"mean": mean, "std": math.sqrt(sum((v - mean) ** 2 for v in values) / len(values)),
            "sample_size": len(values)}


def build(season: int) -> dict:
    selected = {}
    for year in (season - 1, season):
        games = ingest.load_games(year)
        form = features.team_form(games, year)
        players = features.player_lines(ingest.load_players(year))
        prof, provenance = profiles.build_all(year, form, players, {}, exact_season=True)
        measured = coverage.team_contexts(year)
        external = databallr.team_contexts(year)
        pool = [p for roster in players.values() for p in roster
                if (p.get("games") or 0) > 0 and (p.get("mpg") or 0) >= 10]
        baselines = {key: distribution(p.get(key) for p in pool)
                     for key in ("ppg", "rpg", "apg", "three_pm", "ts_pct")}
        selected[str(year)] = {
            "season": year, "season_label": profiles.season_label(year),
            "data_through_utc": max((g.date_utc for g in games), default=None),
            "sources": provenance, "databallr": databallr.summary(year),
            "player_baselines": baselines,
            "player_policy": "Same-season players with 10+ minutes per game; production is not an overall grade.",
            "teams": {team: {"form": form.get(team), "players": players.get(team, []),
                             "profile": prof.get(team, {}), "measured_coverage": measured.get(team),
                             "databallr": external.get(team)} for team in teams.all_abbrs()},
        }
    return {"schema": "chase-basketball-evidence/1", "sport": "nba",
            "generated_at_utc": datetime.now(timezone.utc).isoformat(), "seasons": selected}


def write(out: Path, season: int) -> dict:
    from .export import forbidden_keys

    payload = build(season)
    leaked = forbidden_keys(payload)
    if leaked:
        raise ValueError(f"Private fields in factual evidence: {sorted(leaked)}")
    out.mkdir(parents=True, exist_ok=True)
    (out / "evidence.json").write_text(json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8")
    return {"seasons": list(payload["seasons"]), "teams": sum(len(s["teams"]) for s in payload["seasons"].values())}
