"""Five-man units and situational splits for the public desk (2026-11-18 audit)."""
from types import SimpleNamespace

from nbamodel import lineups


def test_units_keep_the_most_used_fives_over_the_minutes_floor():
    rows = [{"Name": "A, B, C, D, E", "Minutes": 120, "OffPoss": 250, "DefPoss": 248, "Points": 290,
             "OpponentPoints": 260},
            {"Name": "A, B, C, D, F", "Minutes": 40, "OffPoss": 80, "DefPoss": 80, "Points": 80, "OpponentPoints": 96},
            {"Name": "G, H, I, J, K", "Minutes": 9, "OffPoss": 20, "DefPoss": 20, "Points": 30, "OpponentPoints": 10}]
    out = lineups.units(rows)
    assert len(out) == 2                                   # the 9-minute five is under the floor
    top = out[0]
    assert top["players"] == ["A", "B", "C", "D", "E"]
    assert (top["off_rating"], top["def_rating"]) == (116.0, 104.8)
    assert top["net_rating"] == 11.2
    assert out[1]["net_rating"] == -20.0


def test_lineup_fetch_retries_once_and_leaves_a_failing_club_out():
    calls = []

    def fetch(url):
        calls.append(url)
        if "1610612752" in url and len([c for c in calls if "1610612752" in c]) == 1:
            raise OSError("502")
        if "1610612761" in url:
            raise OSError("down")
        return {"multi_row_table_data": [{"Name": "A, B, C, D, E", "Minutes": 50, "OffPoss": 100,
                                          "DefPoss": 100, "Points": 110, "OpponentPoints": 100}]}
    out = lineups.fetch_lineups(2027, ["NY", "TOR", "XXX"], fetch=fetch)
    assert list(out) == ["NY"]                             # retried past the 502; Toronto left out
    assert "Season=2026-27" in calls[0]


def game(day, home, away, hp, ap, neutral=0):
    box = {"fga": 88, "oreb": 10, "tov": 14, "fta": 22}
    return SimpleNamespace(date_utc=f"2026-11-{day:02d}T00:00Z", home=home, away=away, neutral=neutral,
                           home_pts=hp, away_pts=ap, **{f"home_{k}": v for k, v in box.items()},
                           **{f"away_{k}": v for k, v in box.items()})


def test_splits_by_site_and_rest():
    games = [game(1, "NY", "TOR", 110, 100), game(2, "BOS", "NY", 105, 100), game(5, "NY", "BOS", 120, 100)]
    out = lineups.splits(games)
    ny = out["NY"]
    assert ny["games"] == {"at_home": 2, "on_road": 1, "back_to_back": 1, "rested": 1}
    poss = 88 - 10 + 14 + 0.44 * 22
    assert ny["at_home"]["value"] == round(100 * (230 - 200) / (2 * poss), 1)
    assert ny["back_to_back"]["value"] < 0 < ny["rested"]["value"]     # lost the second night, won rested
    assert ny["at_home"]["rank"] == 1


def test_lineup_fetch_stops_after_two_clubs_in_a_row_fail():
    asked = []

    def fetch(url):
        asked.append(url)
        raise OSError("timed out")
    out = lineups.fetch_lineups(2027, ["ATL", "BOS", "CHI", "DAL"], fetch=fetch)
    assert out == {}
    assert len(asked) == 4          # ATL and BOS, two tries each; CHI and DAL never asked
