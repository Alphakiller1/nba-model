"""Offline tests: parsing, the rating fit, availability, the public boundary, the gate."""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from nbamodel import authority, availability, export, ingest, ledger, live, ratings, teams

FIX = Path(__file__).parent / "fixtures"


def _game(eid, day, home, away, hp, ap, poss=100, neutral=0):
    # Box chosen so possessions() == poss exactly: fga - oreb + tov + 0.44 * fta.
    box = dict(fgm=40, fga=poss - 10, **{"3pm": 12, "3pa": 35}, ftm=15, fta=25, oreb=10, dreb=33, ast=24, tov=9)
    box["fga"] = int(poss + box["oreb"] - box["tov"] - 0.44 * box["fta"])
    kw = {f"home_{k}": v for k, v in box.items()} | {f"away_{k}": v for k, v in box.items()}
    return ingest.Game(event_id=str(eid), season=2026, date_utc=day.strftime("%Y-%m-%dT%H:%MZ"),
                       home=home, away=away, neutral=neutral, home_pts=hp, away_pts=ap, **kw)


def test_parse_summary_reads_box_and_close():
    data = json.loads((FIX / "summary_401810436.json").read_text(encoding="utf-8"))
    game, players = ingest.parse_summary("401810436", 2026, data)
    assert (game.away, game.home) == ("OKC", "HOU")
    assert (game.away_pts, game.home_pts) == (111, 91)
    assert game.away_fga == 89 and game.away_tov == 12  # team turnovers included
    # ESPN quotes the HOME handicap: HOU +4.5 means OKC -4.5.
    assert float(game.dk_spread) == 4.5 and float(game.dk_total) == 221.5
    assert 85 < game.possessions() < 110
    assert any(p.name == "Chet Holmgren" and p.team == "OKC" for p in players)


def test_scoreboard_spread_becomes_home_margin():
    data = json.loads((FIX / "scoreboard_20261021.json").read_text(encoding="utf-8"))
    fx = {f"{f.away}@{f.home}": f for f in live.parse_scoreboard(data)}
    ind_no = fx["IND@NO"]  # "IND -3.5": the road side is favoured
    assert ind_no.book["spread"] == 3.5 and ind_no.book["margin"] == -3.5
    atl_orl = fx["ATL@ORL"]  # "ORL -3.5"
    assert atl_orl.book["margin"] == 3.5


def test_fit_recovers_planted_ratings():
    rng = random.Random(7)
    truth = {a: rng.uniform(-6, 6) for a in ratings.ABBRS}
    mean = sum(truth.values()) / len(truth)
    truth = {a: v - mean for a, v in truth.items()}
    start = datetime(2025, 11, 1, 23, tzinfo=timezone.utc)
    games, eid = [], 0
    for d in range(120):
        order = ratings.ABBRS[:]
        rng.shuffle(order)
        for i in range(0, 30, 2):
            h, a = order[i], order[i + 1]
            eid += 1
            # Each club's net splits into scoring (+t/2) and preventing (-t/2).
            hp = round(112 + truth[h] / 2 - truth[a] / 2 + 1.0)
            ap = round(112 + truth[a] / 2 - truth[h] / 2 - 1.0)
            games.append(_game(eid, start + timedelta(days=d), h, a, hp, ap))
    const = ratings.Constants(1.0, 0.0, 0.0, 12.0, 18.0)
    r = ratings.fit(games, as_of=start + timedelta(days=200), prior=ratings.prior_from(None, ratings.PARAMS),
                    params=ratings.PARAMS, const=const)
    # Scores are whole points and the ridge shrinks toward a zero prior, so
    # recovery is close, not exact: tight in rank order, within ~2 points.
    err = [abs(r.net(a) - truth[a]) for a in ratings.ABBRS]
    assert max(err) < 2.5 and sum(err) / len(err) < 1.0
    xs = [truth[a] for a in ratings.ABBRS]
    ys = [r.net(a) for a in ratings.ABBRS]
    mx, my = sum(xs) / 30, sum(ys) / 30
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    corr = cov / (sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys)) ** 0.5
    assert corr > 0.97
    best = max(truth, key=truth.get)
    assert max(ratings.ABBRS, key=r.net) == best


def test_projection_is_symmetric_on_neutral_floor():
    z = {a: 0.0 for a in ratings.ABBRS}
    r = ratings.Ratings("t", 114.0, 100.0, dict(z), dict(z), dict(z))
    p = ratings.project(r, "BOS", "NY", const=ratings.CONSTANTS, neutral=True)
    assert p.margin == pytest.approx(0.0) and p.win_probability == pytest.approx(0.5)
    home = ratings.project(r, "BOS", "NY", const=ratings.CONSTANTS)
    assert home.margin > 0  # home court
    tired = ratings.project(r, "BOS", "NY", const=ratings.CONSTANTS, home_b2b=1)
    assert tired.margin < home.margin  # a back-to-back costs the tired side


def test_fitted_constants_have_basketball_signs():
    c = ratings.CONSTANTS
    assert c.home_eff > 0 and c.b2b_off < 0 and c.b2b_def > 0
    assert 10 < c.margin_sigma < 18 and 14 < c.total_sigma < 24


def test_rest_flags_mark_second_night():
    d0 = datetime(2025, 11, 1, 23, 30, tzinfo=timezone.utc)
    g1 = _game(1, d0, "BOS", "NY", 110, 100)
    g2 = _game(2, d0 + timedelta(days=1), "MIA", "BOS", 110, 100)
    g3 = _game(3, d0 + timedelta(days=3), "BOS", "MIA", 110, 100)
    flags = ratings.rest_flags([g1, g2, g3])
    assert flags[("2", "BOS")] == 1 and flags[("2", "MIA")] == 0 and flags[("3", "BOS")] == 0


def test_roster_value_caps_at_one_floor():
    vals = {str(i): (0.1, 0.15) for i in range(10)}  # ten 36-minute players
    assert availability.roster_value(vals, vals) == pytest.approx(0.1)
    assert availability.roster_value([], vals) == 0.0


def test_returning_player_keeps_older_value():
    # A full season: 69 games of 34 minutes at 0.55 Game Score per minute.
    old = [availability.Line(f"e{i}", "IND", "hali", "Tyrese Haliburton", 34.0, 18.7) for i in range(69)]
    vals = availability.season_values([], old)
    assert vals["hali"][0] > 0.25
    assert availability.season_values(old[:1], old)["hali"][0] < vals["hali"][0]  # last season wins


def test_public_slate_boundary_rejects_model_fields():
    assert export.forbidden_keys({"games": [{"away_form": {"rates": {}}}]}) == set()
    leak = {"games": [{"id": "1", "nested": {"win_probability": 0.6}}]}
    assert export.forbidden_keys(leak) == {"win_probability"}


def test_authority_is_research_only_and_withholds_edges():
    assert authority.LEVEL == "RESEARCH_ONLY"
    assert "live_record" in authority.UNMET_GATES
    rec = authority.record()
    assert rec["model_margin_mae_priced"] > rec["market_margin_mae"]  # the record says so
    assert "beats_close_margin" in authority.UNMET_GATES


def test_ledger_freezes_at_tip_and_grades():
    board = {"generated_at_utc": "2026-10-21T15:00:00+00:00", "games": [
        {"id": "9", "away": "IND", "home": "NO", "kickoff_utc": "2026-10-22T00:00Z", "game_state": "scheduled",
         "model_margin": 2.0, "projected_total": 230.0, "win_probability": 0.56, "market_margin": -3.5,
         "market_total": 239.5, "published_margin": -3.5}]}
    rows = ledger.update([], board)
    board["games"][0].update(game_state="live", model_margin=99.0)
    rows = ledger.update(rows, board)
    assert rows[0]["model_margin"] == 2.0
    final = _game(9, datetime(2026, 10, 22, tzinfo=timezone.utc), "NO", "IND", 101, 110)
    rows = ledger.grade(rows, [final])
    s = ledger.summary(rows)
    assert s["graded"] == 1 and s["ats_gap_3"] == {"wins": 0, "bets": 1}


def test_teams_registry_matches_espn_codes():
    assert len(teams.all_abbrs()) == 30
    assert teams.canon("GSW") == "GS" and teams.canon("UTA") == "UTAH"
    assert teams.logo("UTAH").endswith("/utah.png")
