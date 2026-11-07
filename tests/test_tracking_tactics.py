"""Experimental free tracking must retain identity, timing and denominator limits."""

import gzip
import hashlib
import json

import pytest

from nbamodel import dynamics_model, export
from nbamodel import tracking_tactics as t


def payload():
    return {
        "index": {
            "game_id": "0022600001",
            "date": "2026-11-01",
            "players": {"1": {"team": "BOS"}},
            "plays": [{"t": 6000, "n": 1, "p": 1, "type": "3pt", "team": "BOS", "made": True, "f": True}],
        },
        "actions": {
            "on_ball": {"fields": ["t", "handler", "big"], "rows": [[1000, 1, "drop"]]},
            "sets": {"fields": ["t0", "team", "template"], "rows": [[2000, "BOS", "horns"], [3000, "BOS", "high_pnr"]]},
        },
    }


def test_linked_shots_keep_overlapping_sets_and_coverage_separate():
    found = t.linked_attempts(payload())
    assert {(r["kind"], r["label"]) for r in found} == {
        ("coverage", "drop"),
        ("formation", "horns"),
        ("formation", "high_pnr"),
    }
    assert all(r["value"] == 3 and r["made"] == 1 for r in found)


@pytest.mark.parametrize("terminal", ["turnover", "freethrow", "2pt"])
def test_detections_never_cross_intervening_terminal(terminal):
    data = payload()
    data["index"]["plays"].insert(
        0, {"t": 4000, "n": 0, "p": 1, "type": terminal, "team": "BOS", "made": False, "f": False}
    )
    assert t.linked_attempts(data) == []


def test_old_tags_and_period_boundaries_are_excluded():
    data = payload()
    data["index"]["plays"][0]["t"] = 20000
    assert t.linked_attempts(data) == []
    data = payload()
    data["index"]["plays"].insert(0, {"t": 500, "n": 0, "p": 1, "type": "rebound"})
    data["index"]["plays"][-1]["p"] = 2
    assert t.linked_attempts(data) == []


def test_receipt_time_season_hash_and_efficiency_contract(tmp_path, monkeypatch):
    raw = json.dumps(payload()).encode()
    game = {
        "id": "0022600001",
        "date": "2026-11-01",
        "home": "NYK",
        "away": "BOS",
        "complete": True,
        "retrieved_at_utc": "2026-11-06T00:00Z",
        "sha256": hashlib.sha256(raw).hexdigest(),
    }
    manifest = {"source": "fixture", "games": [game], "label_quality": "experimental"}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    (tmp_path / "0022600001.json.gz").write_bytes(gzip.compress(raw))
    monkeypatch.setattr(t, "DATA", tmp_path)
    assert t.contexts(2026, "2026-11-07T00:00Z") == {}
    assert t.contexts(2027, "2026-11-05T00:00Z") == {}
    result = t.contexts(2027, "2026-11-07T00:00Z")
    assert result["BOS"]["forecast_weight"] == 0
    row = next(r for r in result["BOS"]["rows"] if r["kind"] == "coverage")
    assert row["points_per_fga"] == 3 and row["fg_pct"] == 1
    assert not row["sample_qualified"] and "ppp" not in row
    (tmp_path / "0022600001.json.gz").write_bytes(gzip.compress(b"{}"))
    with pytest.raises(ValueError, match="receipt"):
        t.contexts(2027, "2026-11-07T00:00Z")


def test_partial_refresh_does_not_invalidate_old_manifest(tmp_path, monkeypatch):
    data = payload()
    raw = json.dumps(data).encode()
    old = {
        "retrieved_at_utc": "2026-11-01T00:00Z",
        "games": [
            {"id": "0022600001", "complete": False, "date": "2026-11-01", "sha256": hashlib.sha256(raw).hexdigest()}
        ],
    }
    old_text = json.dumps(old)
    (tmp_path / "manifest.json").write_text(old_text)
    (tmp_path / "0022600001.json.gz").write_bytes(gzip.compress(raw))
    monkeypatch.setattr(t, "DATA", tmp_path)

    def fake_get(path):
        if path == "/games":
            return json.dumps(
                [
                    {"id": "0022600001", "date": "2026-11-01", "complete": True},
                    {"id": "0022600002", "date": "2026-11-01", "complete": True},
                ]
            ).encode()
        if path.endswith("0022600002"):
            raise RuntimeError("source unavailable")
        return raw

    monkeypatch.setattr(t, "get", fake_get)
    with pytest.raises(RuntimeError):
        t.refresh(2027, force=True)
    assert (tmp_path / "manifest.json").read_text() == old_text
    assert gzip.decompress((tmp_path / "0022600001.json.gz").read_bytes()) == raw


def test_new_risk_fields_cannot_leak_to_factual_slate():
    assert export.forbidden_keys({"games": [{"nested": {"model_diagnostics": {"shooting_reversion": {}}}}]})
    context = dynamics_model.forecast_diagnostics({}, {}, {"away_points": 100, "home_points": 100})
    assert context["state"] == "unavailable"
