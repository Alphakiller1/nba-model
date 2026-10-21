"""Schedule context: rest, back-to-backs, 3-in-4s, travel, time zones, altitude.

Every game is placed at its home club's arena (the handful of neutral-site
games - Mexico City, Paris, the NBA Cup final - are rare enough to ignore).
Travel is the great-circle distance from the arena of a club's previous game.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import timedelta
from typing import Sequence

from .ingest import Game
from .ratings import local_day

# lat, lon, UTC offset (standard time), elevation in feet
ARENAS = {
    "ATL": (33.757, -84.396, -5, 1050), "BOS": (42.366, -71.062, -5, 20), "BKN": (40.683, -73.975, -5, 30),
    "CHA": (35.225, -80.839, -5, 750), "CHI": (41.881, -87.674, -6, 600), "CLE": (41.497, -81.688, -5, 650),
    "DAL": (32.790, -96.810, -6, 430), "DEN": (39.749, -105.008, -7, 5280), "DET": (42.341, -83.055, -5, 600),
    "GS": (37.768, -122.388, -8, 10), "HOU": (29.751, -95.362, -6, 50), "IND": (39.764, -86.156, -5, 715),
    "LAC": (33.945, -118.343, -8, 100), "LAL": (34.043, -118.267, -8, 270), "MEM": (35.138, -90.051, -6, 340),
    "MIA": (25.781, -80.187, -5, 10), "MIL": (43.045, -87.917, -6, 620), "MIN": (44.980, -93.276, -6, 830),
    "NO": (29.949, -90.082, -6, 5), "NY": (40.751, -73.993, -5, 30), "OKC": (35.463, -97.515, -6, 1200),
    "ORL": (28.539, -81.384, -5, 100), "PHI": (39.901, -75.172, -5, 40), "PHX": (33.446, -112.071, -7, 1090),
    "POR": (45.532, -122.667, -8, 50), "SAC": (38.580, -121.500, -8, 30), "SA": (29.427, -98.438, -6, 650),
    "TOR": (43.643, -79.379, -5, 250), "UTAH": (40.768, -111.901, -7, 4226), "WSH": (38.898, -77.021, -5, 50),
}
HIGH = {"DEN", "UTAH"}


def km(a: str, b: str) -> float:
    (la1, lo1, _, _), (la2, lo2, _, _) = ARENAS[a], ARENAS[b]
    p1, p2 = math.radians(la1), math.radians(la2)
    dp, dl = p2 - p1, math.radians(lo2 - lo1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(h))


@dataclass(frozen=True)
class Side:
    rest: int            # full days off before this game (0 = back-to-back), capped at 4
    b2b: int
    three_in_four: int   # this is the third game in four days
    travel_km: float
    tz_shift: int        # hours moved since the previous game (absolute)
    games_7: int         # games in the previous seven days


def schedule_context(games: Sequence[Game]) -> dict[tuple[str, str], Side]:
    """(event_id, team) -> Side, from the season's full schedule of finals."""
    by_team: dict[str, list[Game]] = {}
    for g in sorted(games, key=lambda g: g.date_utc):
        by_team.setdefault(g.home, []).append(g)
        by_team.setdefault(g.away, []).append(g)
    out: dict[tuple[str, str], Side] = {}
    for team, seq in by_team.items():
        days = [local_day(g.date_utc) for g in seq]
        for i, g in enumerate(seq):
            if i == 0:
                out[(g.event_id, team)] = Side(4, 0, 0, 0.0, 0, 0)
                continue
            prev = seq[i - 1]
            rest = max(0, min(4, (days[i] - days[i - 1]).days - 1))
            three = int(i >= 2 and (days[i] - days[i - 2]) <= timedelta(days=3))
            g7 = sum(1 for d in days[:i] if days[i] - d <= timedelta(days=7))
            here, there = g.home, prev.home
            out[(g.event_id, team)] = Side(rest, int(rest == 0), three, km(there, here),
                                           abs(ARENAS[here][2] - ARENAS[there][2]), g7)
    return out


def features(g: Game, ctx: dict[tuple[str, str], Side]) -> dict[str, float]:
    """Home-minus-away schedule features for one game."""
    h = ctx.get((g.event_id, g.home), Side(2, 0, 0, 0.0, 0, 2))
    a = ctx.get((g.event_id, g.away), Side(2, 0, 0, 0.0, 0, 2))
    return {
        "rest_diff": h.rest - a.rest,
        "b2b_diff": h.b2b - a.b2b,
        "three_in_four_diff": h.three_in_four - a.three_in_four,
        "travel_diff_1000km": (h.travel_km - a.travel_km) / 1000.0,
        "tz_diff": h.tz_shift - a.tz_shift,
        "games7_diff": h.games_7 - a.games_7,
        # A road club visiting altitude without having played there the day before.
        "altitude": 1.0 if g.home in HIGH and not g.neutral else 0.0,
        "neutral": float(g.neutral),
    }
