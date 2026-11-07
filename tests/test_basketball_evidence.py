"""Free-source contracts: direction, samples, season and competition boundaries."""
from datetime import datetime, timezone

import pytest

from nbamodel import coverage, databallr


def test_both_committed_seasons_have_complete_verified_receipts():
    for year in (2026, 2027):
        data = databallr.snapshot(year)
        assert data["manifest"]["season"] == year
        assert len(databallr.team_contexts(year)) == 30
        assert all(int(p.get("playoffs") or 0) == 0 for p in databallr.regular_players(year, advanced=True))
        assert databallr.regular_players(year, as_of=datetime(2025, 1, 1, tzinfo=timezone.utc)) == []


def test_advanced_regular_filter_keeps_player_team_identity(monkeypatch):
    rows = [{"nba_id": 1, "TeamId": 2, "playoffs": p} for p in (0, 1)]
    monkeypatch.setattr(databallr, "snapshot", lambda year: {
        "manifest": {"retrieved_at_utc": "2026-11-06T00:00:00+00:00"}, "advanced": rows})
    assert databallr.regular_players(2027, advanced=True) == rows[:1]


def test_coverage_rejects_wrong_season_direction_and_unreconciled_points():
    payload = coverage.load(2027, "defense")["payload"]
    with pytest.raises(ValueError, match="season"):
        coverage.validate(payload, 2026, "defense")
    with pytest.raises(ValueError, match="direction"):
        coverage.validate(payload, 2027, "offense")
    import copy
    bad = copy.deepcopy(payload)
    row = next(r for r in bad["teamZoneDefense"] if r["POSS_ZONE"] > 0)
    row["POSS_ZONE_PTS"] += 100
    with pytest.raises(ValueError, match="reconcile"):
        coverage.validate(bad, 2027, "defense")


def test_coverage_direction_and_thin_samples_are_preserved():
    for year in (2026, 2027):
        context = coverage.team_contexts(year)
        assert len(context) == 30
        for team in context.values():
            for side in ("offense", "defense"):
                block = team[side]
                assert sum(r["possessions"] for r in block["rows"]) == block["possessions"]
                assert block["rows"][1]["label"] == "Non-zone"
                for row in block["rows"]:
                    if row["possessions"] < coverage.MIN_POSS:
                        assert row["ppp_grade"] is None
                    else:
                        assert row["ppp_grade"]["better"] == ("low" if side == "defense" else "high")
                    if not row["possessions"]:
                        assert row["ppp"] is None


def test_databallr_zone_schema_keeps_counts_and_provider_names():
    team = next(iter(databallr.team_contexts(2027).values()))
    assert team["schema"] == "databallr-pbp-zones-v1"
    for side in ("offense", "defense"):
        zones = team[side]["zones"]
        assert "Short mid-range" in {z["label"] for z in zones}
        assert all(z["made"] <= z["attempts"] for z in zones)
        assert sum(z["shot_share"] for z in zones) == pytest.approx(1)
