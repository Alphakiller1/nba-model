"""Stints, RAPM, markets consensus, schedule context."""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

import pytest

from nbamodel import context, markets, rapm, stints
from test_model import _game


def _play(seq, period, clock, kind, team, parts=(), hs=None, as_=None, shooting=False):
    return {"sequenceNumber": str(seq), "period": {"number": period}, "clock": {"displayValue": clock},
            "type": {"text": kind}, "team": {"id": team}, "participants": [{"athlete": {"id": p}} for p in parts],
            "homeScore": hs, "awayScore": as_, "shootingPlay": shooting}


def _summary(plays):
    home = [f"h{i}" for i in range(7)]
    away = [f"a{i}" for i in range(7)]

    def block(tid, ids):
        return {"team": {"id": tid}, "statistics": [{"athletes": [
            {"athlete": {"id": p}, "starter": i < 5} for i, p in enumerate(ids)]}]}
    return {"header": {"competitions": [{"competitors": [{"id": "1", "homeAway": "home"},
                                                         {"id": "2", "homeAway": "away"}]}]},
            "boxscore": {"players": [block("1", home), block("2", away)]}, "plays": plays}


def test_stints_split_on_substitution_and_keep_every_point():
    plays = [
        _play(1, 1, "11:30", "Jump Shot", "1", ["h0"], 2, 0, True),
        _play(2, 1, "11:00", "Defensive Rebound", "2", ["a1"], 2, 0),
        _play(3, 1, "10:00", "Substitution", "1", ["h5", "h0"], 2, 0),
        _play(4, 1, "10:00", "Free Throw - 1 of 2", "2", ["a0"], 2, 1),  # same clock as the sub
        _play(5, 1, "9:00", "Layup Shot", "1", ["h5"], 4, 1, True),
    ]
    out = stints.parse("e1", _summary(plays))
    assert sum(s.home_pts for s in out) == 4 and sum(s.away_pts for s in out) == 1
    assert "h0" in out[0].home_players and "h5" in out[-1].home_players and "h0" not in out[-1].home_players
    assert all(len(s.home_players) == 5 and len(s.away_players) == 5 for s in out)


def test_second_period_lineup_is_inferred_from_who_acts():
    plays = [_play(1, 1, "11:00", "Jump Shot", "1", ["h0"], 2, 0, True),
             # Period 2 opens with h5/h6 on the floor in place of h3/h4: never logged.
             _play(2, 2, "11:00", "Jump Shot", "1", ["h5"], 4, 0, True),
             _play(3, 2, "10:30", "Jump Shot", "1", ["h6"], 6, 0, True),
             _play(4, 2, "10:00", "Substitution", "1", ["h3", "h0"], 6, 0)]
    out = stints.parse("e1", _summary(plays))
    p2 = [s for s in out if "h5" in s.home_players]
    assert p2 and "h6" in p2[0].home_players


def test_rapm_finds_the_planted_star():
    rng = random.Random(3)
    home_pool = [f"h{i}" for i in range(8)]
    away_pool = [f"a{i}" for i in range(8)]
    t0 = datetime(2025, 11, 1, tzinfo=timezone.utc).timestamp()
    rows = []
    for i in range(3000):
        h = tuple(rng.sample(home_pool, 5))
        a = tuple(rng.sample(away_pool, 5))
        star = 8.0 if "h0" in h else 0.0
        rate = 1.12 + star / 100
        rows.append(rapm.StintRow(t0 + i * 600, 2026, h, a, int(round(rate * 20 + rng.gauss(0, 2))),
                                  int(round(1.12 * 20 + rng.gauss(0, 2))), 20.0))
    p = rapm.RapmParams(ridge=200.0)
    r = rapm.Design(rows, 2026, {}, p).fit(t0 + 3001 * 600)
    best = max(home_pool, key=lambda x: r.off[x])
    assert best == "h0"
    assert r.off["h0"] - sum(r.off[x] for x in home_pool[1:]) / 7 > 4.0


def test_market_consensus_drops_live_books_and_takes_the_median():
    def item(name, spread):
        return {"provider": {"name": name}, "homeTeamOdds": {"open": {"pointSpread": {"american": f"{spread:+}"}},
                                                           "close": {"pointSpread": {"american": f"{spread:+}"}}},
                "awayTeamOdds": {}, "open": {"total": {"american": "220.5"}}, "close": {"total": {"american": "221"}}}
    c = markets.consensus([item("A", -3.5), item("B", -4.5), item("C", -4.0), item("C - Live Odds", -15.0)])
    assert c["books"] == 3 and c["spread_close"] == -4.0 and c["total_close"] == 221.0


def test_schedule_context_sees_back_to_back_and_travel():
    d = datetime(2025, 11, 1, 23, tzinfo=timezone.utc)
    g1 = _game(1, d, "BOS", "LAL", 110, 100)
    g2 = _game(2, d + timedelta(days=1), "NY", "LAL", 110, 100)
    ctx = context.schedule_context([g1, g2])
    lal = ctx[("2", "LAL")]
    assert lal.b2b == 1 and 250 < lal.travel_km < 330
    f = context.features(g2, ctx)
    assert f["b2b_diff"] == -1  # home NY rested, LAL on the second night
    assert context.features(_game(3, d, "DEN", "MIA", 1, 0), ctx)["altitude"] == 1.0


def test_km_is_symmetric_and_sane():
    assert context.km("BOS", "LAL") == pytest.approx(context.km("LAL", "BOS"))
    assert 4000 < context.km("BOS", "LAL") < 4300


def test_signal_tiers_and_sides():
    from nbamodel import signals
    rec = {"total": {"strong": {"hit": 0.567}}, "spread": {"strong": {"hit": 0.541}}}
    out = signals.for_game(model_margin=-2.0, market_margin=4.5, model_total=227.0, market_total=234.5,
                           home="LAL", away="GS", rec=rec)
    by = {s["market"]: s for s in out}
    assert by["total"]["side"] == "under" and by["total"]["tier"] == "strong"
    assert by["spread"]["side"] == "GS" and by["spread"]["line"] == -4.5  # LAL -4.5, model has GS
    assert signals.for_game(1.0, 0.0, 230.0, 228.5, "A", "B", rec) == []  # below every threshold
    assert signals.classify("spread", 5.0) is None  # 4-6 spreads are not a signal


def test_ledger_grades_signals_at_the_line_they_fired_on():
    from nbamodel import ledger
    g = {"id": "9", "away": "GS", "home": "LAL", "kickoff_utc": "2026-10-22T02:00Z", "game_state": "scheduled",
         "model_margin": 0.0, "projected_total": 227.0, "win_probability": 0.5, "market_margin": 4.5,
         "market_total": 234.5, "published_margin": 4.5,
         "signals": [{"market": "total", "side": "under", "tier": "strong", "gap": -7.5, "line": 234.5}]}
    rows = ledger.update([], {"generated_at_utc": "t0", "games": [g]})
    g2 = dict(g, market_total=231.5, signals=[])
    rows = ledger.update(rows, {"generated_at_utc": "t1", "games": [g2]})
    rows = ledger.grade(rows, [_game(9, datetime(2026, 10, 22, tzinfo=timezone.utc), "LAL", "GS", 110, 112)])
    s = ledger.signal_summary(rows)
    assert s["clv_toward"] == 1.0  # 234.5 -> 231.5, toward the under
    assert s["signals"] == 1 and s["wins"] == 1  # 222 < 234.5
