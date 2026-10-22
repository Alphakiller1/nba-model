"""Player prop layer and the pregame-line backfill."""
from __future__ import annotations

from nbamodel import live
from nbamodel import player_props as pp


def test_simulate_shapes_and_bounds():
    rates = {"points": 0.6, "rebounds": 0.2, "assists": 0.15, "threes": 0.06}
    s = pp.simulate(rates, 32.0, 4.0, 1.0, sims=2000, seed=1)
    assert set(s) == {"points", "rebounds", "assists", "threes", "pra"}
    assert 17 < s["points"]["mean"] < 21.5
    assert s["points"]["p10"] <= s["points"]["p50"] <= s["points"]["p90"]
    assert s["points"]["q_levels"] == list(pp.Q_LEVELS)
    assert abs(sum(s["threes"]["pmf"].values()) - 1.0) < 0.01
    pra = s["pra"]["mean"]
    assert abs(pra - (s["points"]["mean"] + s["rebounds"]["mean"] + s["assists"]["mean"])) < 0.5


def test_points_scale_moves_only_points():
    rates = {"points": 0.5, "rebounds": 0.2, "assists": 0.1, "threes": 0.05}
    a = pp.simulate(rates, 30.0, 3.0, 1.0, 1.0, sims=1500, seed=2)
    b = pp.simulate(rates, 30.0, 3.0, 1.0, 1.1, sims=1500, seed=2)
    assert b["points"]["mean"] > a["points"]["mean"]
    assert abs(b["rebounds"]["mean"] - a["rebounds"]["mean"]) < 0.4


def test_project_game_ties_points_to_team_score():
    book = pp.RateBook(2026)
    pids = sorted(book.lines, key=lambda k: -len(book.lines[k]))[:10]
    rot = [{"player_id": p, "name": "", "minutes": 24.0} for p in pids]
    game = {"id": "1", "key": "AAA@BBB", "away": "BOS", "home": "NY", "projected_possessions": 99.5,
            "projected_away_score": 100.0, "projected_home_score": 140.0,
            "rotation": {"away": rot[:5], "home": rot[5:]}}
    rows = pp.project_game(book, game)
    assert len(rows) == 10
    for side in ("away", "home"):
        scales = {r["points_scale"] for r in rows if r["home"] == (side == "home")}
        assert len(scales) == 1
        assert pp.POINTS_BOUND[0] <= scales.pop() <= pp.POINTS_BOUND[1]
    assert all(r["game_key"] == "AAA@BBB" and r["stats"]["points"]["mean"] > 0 for r in rows)


def test_backfill_takes_the_summary_line_after_tip(monkeypatch):
    f = live.Fixture.__new__(live.Fixture)
    f.event_id, f.state, f.book = "9", "final", {}
    monkeypatch.setattr(live.espn, "summary", lambda eid: {"pickcenter": [
        {"provider": {"name": "DraftKings"}, "spread": -2.5, "overUnder": 233.5,
         "homeTeamOdds": {"moneyLine": -135}, "awayTeamOdds": {"moneyLine": 115}}]})
    live.backfill_books([f])
    assert f.book["total"] == 233.5 and f.book["margin"] == 2.5
    assert f.book["source"].startswith("ESPN game summary")
    s = live.Fixture.__new__(live.Fixture)
    s.event_id, s.state, s.book = "10", "scheduled", {}
    live.backfill_books([s])
    assert s.book == {}
