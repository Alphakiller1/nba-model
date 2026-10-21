"""The 30 NBA clubs under ESPN's ids and abbreviations.

ESPN's abbreviations are the canonical codes everywhere in this repo (GS, NY,
NO, SA, UTAH, WSH), because they are what the scoreboard, the injury feed and
the logo CDN all key on. ``ALIASES`` maps the common alternatives onto them.
"""
from __future__ import annotations

# id, abbr, full name, short name, primary colour (ESPN)
TEAMS: tuple[tuple[str, str, str, str, str], ...] = (
    ("1", "ATL", "Atlanta Hawks", "Hawks", "#c8102e"),
    ("2", "BOS", "Boston Celtics", "Celtics", "#008348"),
    ("17", "BKN", "Brooklyn Nets", "Nets", "#000000"),
    ("30", "CHA", "Charlotte Hornets", "Hornets", "#008ca8"),
    ("4", "CHI", "Chicago Bulls", "Bulls", "#ce1141"),
    ("5", "CLE", "Cleveland Cavaliers", "Cavaliers", "#860038"),
    ("6", "DAL", "Dallas Mavericks", "Mavericks", "#0064b1"),
    ("7", "DEN", "Denver Nuggets", "Nuggets", "#0e2240"),
    ("8", "DET", "Detroit Pistons", "Pistons", "#1d428a"),
    ("9", "GS", "Golden State Warriors", "Warriors", "#fdb927"),
    ("10", "HOU", "Houston Rockets", "Rockets", "#ce1141"),
    ("11", "IND", "Indiana Pacers", "Pacers", "#002d62"),
    ("12", "LAC", "LA Clippers", "Clippers", "#c8102e"),
    ("13", "LAL", "Los Angeles Lakers", "Lakers", "#552583"),
    ("29", "MEM", "Memphis Grizzlies", "Grizzlies", "#5d76a9"),
    ("14", "MIA", "Miami Heat", "Heat", "#98002e"),
    ("15", "MIL", "Milwaukee Bucks", "Bucks", "#00471b"),
    ("16", "MIN", "Minnesota Timberwolves", "Timberwolves", "#266092"),
    ("3", "NO", "New Orleans Pelicans", "Pelicans", "#0a2240"),
    ("18", "NY", "New York Knicks", "Knicks", "#1d428a"),
    ("25", "OKC", "Oklahoma City Thunder", "Thunder", "#007ac1"),
    ("19", "ORL", "Orlando Magic", "Magic", "#0077c0"),
    ("20", "PHI", "Philadelphia 76ers", "76ers", "#1d428a"),
    ("21", "PHX", "Phoenix Suns", "Suns", "#29127a"),
    ("22", "POR", "Portland Trail Blazers", "Trail Blazers", "#e03a3e"),
    ("23", "SAC", "Sacramento Kings", "Kings", "#5a2d81"),
    ("24", "SA", "San Antonio Spurs", "Spurs", "#c4ced4"),
    ("28", "TOR", "Toronto Raptors", "Raptors", "#d91244"),
    ("26", "UTAH", "Utah Jazz", "Jazz", "#4e008e"),
    ("27", "WSH", "Washington Wizards", "Wizards", "#e31837"),
)

ALIASES = {
    "GSW": "GS", "NYK": "NY", "NOP": "NO", "SAS": "SA", "UTA": "UTAH", "WAS": "WSH",
    "PHO": "PHX", "BRK": "BKN", "CHO": "CHA",
}

BY_ID = {t[0]: t for t in TEAMS}
BY_ABBR = {t[1]: t for t in TEAMS}


def canon(abbr: str) -> str:
    code = str(abbr or "").strip().upper()
    return ALIASES.get(code, code)


def all_abbrs() -> list[str]:
    return [t[1] for t in TEAMS]


def name(abbr: str) -> str:
    return BY_ABBR[canon(abbr)][2]


def short(abbr: str) -> str:
    return BY_ABBR[canon(abbr)][3]


def color(abbr: str) -> str:
    return BY_ABBR[canon(abbr)][4]


def team_id(abbr: str) -> str:
    return BY_ABBR[canon(abbr)][0]


def logo(abbr: str) -> str:
    return f"https://a.espncdn.com/i/teamlogos/nba/500/{canon(abbr).lower()}.png"
