"""Matchup-profile layer: aging, shot geometry, ranks, style, regression, schedule, stats.nba parsing."""
from __future__ import annotations

import io
import json
from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest

from nbamodel import aging, export, nbastats, profiles, stints
from test_model import _game


def test_aging_curve_has_the_biological_shape():
    c = aging.curve()
    assert c.off[20] > 0.5 and c.off[21] > c.off[26]       # young players progress
    assert c.off[31] < -0.2                                 # thirties decline
    ages = sorted(c.off)
    floor = min(ages, key=lambda a: c.off[a])
    assert all(c.off[a] == c.off[floor] for a in ages if a >= floor)  # no late upturn
    assert abs(c.deff[22]) < abs(c.off[22])                 # defense moves less than offense
    assert c.delta(None) == (0.0, 0.0)


@pytest.mark.parametrize("value,dist,x,y,zone", [
    (2, 1.0, 25, 1, "rim"), (2, 9.0, 27, 9, "paint"), (2, 17.0, 10, 12, "mid"),
    (3, 23.0, 2, 4, "corner3"), (3, 25.0, 38, 22, "above3"), (3, 60.0, 25, 60, "heave"),
])
def test_shot_zones_follow_the_league_map(value, dist, x, y, zone):
    assert stints.shot_zone(value, dist, x, y) == zone


@pytest.mark.parametrize("text,kind", [
    ("Driving Layup Shot", "drive"), ("Pullup Jump Shot", "pullup"), ("Step Back Jump Shot", "stepback"),
    ("Cutting Dunk Shot", "cut"), ("Layup Shot Putback", "putback"), ("Turnaround Hook Shot", "post"),
    ("Driving Floating Jump Shot", "floater"), ("Jump Shot", "jumper"),
])
def test_shot_kinds_read_espn_types(text, kind):
    assert stints.shot_kind(text, assisted=True, value=3, distance=24) == kind


def test_ranked_respects_direction():
    r = profiles.ranked({"A": 0.40, "B": 0.30, "C": 0.35}, "x", "low", "pct")
    assert r["B"]["rank"] == 1 and r["A"]["rank"] == 3 and r["A"]["of"] == 3
    n = profiles.ranked({"A": 1.0, "B": 2.0}, "x", "neutral", "num")
    assert n["B"]["rank"] == 1  # neutral ranks most-first


def test_style_tags_only_at_the_extremes_and_carry_their_figure():
    def e(rank, value=0.3):
        return {"rank": rank, "of": 30, "value": value, "label": "x"}
    shots = {"T": {"offense": {"rim": {"share": e(2)}, "mid": {"share": e(15)}}, "defense": {"rim": {"share": e(29)}}}}
    pts = {"T": {"offense": {"Isolation": {"freq": e(14)}}}}
    tags = {t["key"]: t for t in profiles.style("T", shots, pts, {}, {"T": {"rates": {"pace": e(1, 104.0)}}})}
    assert tags["rim_share"]["tag"] == "Attacks The Rim"
    assert tags["rim_allowed"]["tag"] == "Walls Off The Rim"
    assert tags["pace"]["value"] == 104.0  # a rank always sits beside its value
    assert "mid_share" not in tags and "iso" not in tags


def test_regression_measures_luck_and_close_games():
    d = datetime(2025, 11, 1, 23, tzinfo=timezone.utc)
    g1 = _game(1, d, "BOS", "NY", 101, 100)
    g2 = _game(2, d + timedelta(days=2), "BOS", "MIA", 130, 90)
    out = profiles.regression([g1, g2])
    assert out["BOS"]["close_record"] == "1-0"
    assert out["BOS"]["games"] == 2
    assert "opp_three_luck" in out["BOS"] and "wins_over_pythag" in out["BOS"]


def test_schedule_load_counts_density_and_travel():
    d = datetime(2025, 11, 1, 23, tzinfo=timezone.utc)
    games = [_game(1, d, "BOS", "LAL", 1, 0), _game(2, d + timedelta(days=1), "NY", "LAL", 1, 0)]
    s = export.schedule_load(games, "LAL", "DEN", (d + timedelta(days=2)).strftime("%Y-%m-%dT%H:%MZ"))
    assert s["games_last_7"] == 2 and s["three_in_four"] is True
    assert 1500 < s["travel_miles"] < 1700 and s["tz_shift"] == -2 and s["altitude"] is True


def test_stats_nba_two_row_shot_location_header_is_flattened():
    payload = {"resultSets": {"name": "ShotLocations", "headers": [
        {"name": "SHOT_CATEGORY", "columnSpan": 3, "columnsToSkip": 2,
         "columnNames": ["Restricted Area", "Mid-Range"]},
        {"name": "columns", "columnNames": ["TEAM_ID", "TEAM_NAME", "FGM", "FGA", "FG_PCT", "FGM", "FGA", "FG_PCT"]}],
        "rowSet": [[1, "Boston", 10, 15, 0.667, 4, 10, 0.4]]}}
    fake = io.BytesIO(json.dumps(payload).encode())
    with mock.patch("urllib.request.urlopen", return_value=mock.MagicMock(__enter__=lambda s: fake,
                                                                          __exit__=lambda *a: False)):
        rows = nbastats.get("leaguedashteamshotlocations", {})
    assert rows[0]["Restricted Area|FGA"] == 15 and rows[0]["Mid-Range|FG_PCT"] == 0.4


def test_public_slate_profiles_stay_free_of_model_fields():
    prof = {"shot_profile": {"offense": {"rim": {"share": {"value": 0.3, "rank": 3, "of": 30}}}},
            "players": [{"age_change": {"offense": 0.2, "about": "league history"}}]}
    assert export.forbidden_keys({"games": [{"away_profile": prof}]}) == set()


def test_espn_zone_profiles_rank_thirty_clubs_and_split_defense_on_off():
    z = profiles.espn_team_zones(2026)
    assert len(z) == 30
    rim = z["OKC"]["offense"]["rim"]
    assert rim["fg"]["of"] == 30 and 0.5 < rim["fg"]["value"] < 0.8
    assert z["OKC"]["defense"]["rim"]["fg"]["better"] == "low"
    d = profiles.espn_onfloor_defense(2026)
    assert len(d) > 300
    row = next(iter(d.values()))[0]
    assert row["attempts_on"] >= 60 and row["attempts_off"] >= 60
    assert abs(row["diff"] - (row["fg_on"] - row["fg_off"])) < 1e-3


def test_coach_tenure_and_philosophy_come_from_season_pages():
    from nbamodel import coaches
    c = coaches.profile_all(2027)
    assert c["MIL"]["head_coach"] == "Taylor Jenkins" and c["MIL"]["first_season_with_club"] is True
    assert c["MIA"]["season_with_club"] >= 19 and c["MIA"]["first_season_with_club"] is False
    phil = c["MIA"]["philosophy"]
    assert phil["seasons"] >= 6 and phil["summary"].startswith("Spoelstra's teams")
    assert all(0 <= m["percentile"] <= 100 for m in phil["metrics"])
    assert c["ORL"]["philosophy"]["seasons"] == 0  # a first-time head coach has no fingerprint


def test_scheme_reads_carry_evidence_and_skip_zone():
    from nbamodel import schemes
    r = schemes.reads(2026)
    assert len(r) == 30
    for team, v in r.items():
        assert "zone" not in " ".join(x["scheme"].lower() for x in v["reads"])
        for read in v["reads"]:
            assert read["strength"] in ("strong", "clear") and read["evidence"]
    assert r["MIL"]["measured_under"]  # the coach the scheme was measured under is named
