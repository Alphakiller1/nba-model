"""Head coaches: tenure, career, and the philosophy his teams have played with.

Tenure and career come from Basketball-Reference's season coaching pages (one
row per coach per team-season, with seasons at the franchise and overall). It
is the one source that answers a past season with that season's coach.

Philosophy is measured, not written: every team-season a coach ran (41+
games) is placed in league percentiles on sixteen tendencies - pace, shot
diet by distance, corner threes, ball movement, the glass, free throws,
turnovers, and the same things his defenses allowed - and his career average
is taken. A coach who changes teams carries his fingerprint with him; a coach
with no NBA head-coaching seasons has none, and the profile says so.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from . import bbref, teams

FIRST, LAST = 2019, 2026  # completed seasons with team tables
MIN_GAMES = 41

# key, table, column(s), label, phrase when high, phrase when low
METRICS = (
    ("pace", "advanced-team", ("pace",), "Pace", "plays fast", "plays slow"),
    ("three_rate", "shooting-team", ("pct_fga_fg3a",), "Threes Share Of Shots", "lives behind the arc",
     "avoids the three"),
    ("rim_rate", "shooting-team", ("pct_fga_00_03",), "Shots Within 3 Ft", "attacks the rim", "rarely gets to the rim"),
    ("mid_rate", "shooting-team", ("pct_fga_10_16", "pct_fga_16_xx"), "Mid-Range Share (10 Ft To The Arc)",
     "keeps the mid-range", "cuts out the mid-range"),
    ("corner_rate", "shooting-team", ("pct_fg3a_corner",), "Corner Share Of Threes", "hunts the corner three",
     "rarely uses the corners"),
    ("assisted", "shooting-team", ("pct_ast_fg2", "pct_ast_fg3"), "Assisted Field Goals", "moves the ball",
     "creates off the dribble"),
    ("orb", "advanced-team", ("orb_pct",), "Offensive Rebound Rate", "crashes the offensive glass",
     "gets back instead of crashing"),
    ("ftr", "advanced-team", ("fta_per_fga_pct",), "Free Throws Per Shot", "draws fouls", "rarely gets to the line"),
    ("tov", "advanced-team", ("tov_pct",), "Turnover Rate", "lives with turnovers", "protects the ball"),
    ("opp_three_rate", "shooting-opponent", ("opp_pct_fga_fg3a",), "Opponent Threes Share", "concedes threes",
     "runs shooters off the line"),
    ("opp_rim_rate", "shooting-opponent", ("opp_pct_fga_00_03",), "Opponent Shots Within 3 Ft", "concedes the rim",
     "walls off the rim"),
    ("opp_mid_rate", "shooting-opponent", ("opp_pct_fga_10_16", "opp_pct_fga_16_xx"), "Opponent Mid-Range Share",
     "funnels to the mid-range", "takes away the mid-range"),
    ("opp_corner", "shooting-opponent", ("opp_pct_fg3a_corner",), "Opponent Corner Share Of Threes",
     "gives up corner threes", "takes away the corners"),
    ("opp_tov", "advanced-team", ("opp_tov_pct",), "Turnovers Forced", "forces turnovers", "rarely forces turnovers"),
    ("drb", "advanced-team", ("drb_pct",), "Defensive Rebound Rate", "finishes possessions on the glass",
     "gives up second chances"),
    ("opp_ftr", "advanced-team", ("opp_ft_rate",), "Opponent Free Throws Per Shot", "fouls a lot",
     "defends without fouling"),
)


def _f(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _abbr(name: str) -> str | None:
    clean = str(name or "").replace("*", "").strip().lower()
    if clean in bbref.FULL_NAME_ALIASES:
        return bbref.FULL_NAME_ALIASES[clean]
    for t in teams.TEAMS:
        if t[2].lower() == clean:
            return t[1]
    return None


def team_percentiles(year: int) -> dict[str, dict[str, float]]:
    """team -> metric -> league percentile (0-100, higher = more of the thing) for one season."""
    tables = bbref.load_team_seasons(year)
    raw: dict[str, dict[str, float]] = defaultdict(dict)
    for key, table, cols, *_ in METRICS:
        for row in tables.get(table, []):
            team = _abbr(row.get("team"))
            vals = [_f(row.get(c)) for c in cols]
            if team and all(v is not None for v in vals):
                raw[team][key] = sum(vals) / len(vals) if key == "assisted" else sum(vals)
    out: dict[str, dict[str, float]] = defaultdict(dict)
    for key, *_ in METRICS:
        pool = sorted(v[key] for v in raw.values() if key in v)
        n = len(pool)
        for team, v in raw.items():
            if key in v and n > 1:
                below = sum(1 for x in pool if x < v[key])
                out[team][key] = round(100.0 * below / (n - 1), 1)
    return dict(out)


def _norm(name: str) -> str:
    return bbref.norm(name)


def careers(first: int = FIRST, last: int = 2027) -> dict[str, list[dict[str, Any]]]:
    """coach (normalized) -> [{season, team, games, w, l, seasons_franchise, seasons_overall}]"""
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for year in range(first, last + 1):
        for r in bbref.load_coaches(year):
            team = teams.canon(r.get("team", ""))
            out[_norm(r["coach"])].append({
                "season": year, "season_label": f"{year - 1}-{str(year)[2:]}", "team": team, "coach": r["coach"],
                "games": int(_f(r.get("cur_g")) or 0), "w": int(_f(r.get("cur_w")) or 0),
                "l": int(_f(r.get("cur_l")) or 0), "seasons_franchise": int(_f(r.get("seas_num_franch")) or 0),
                "seasons_overall": int(_f(r.get("seas_num_overall")) or 0),
                "career_w": int(_f(r.get("car_w")) or 0), "career_l": int(_f(r.get("car_l")) or 0)})
    return dict(out)


def head_coach(team: str, season: int) -> dict[str, Any] | None:
    """The coach of record now: the season's row for the club with the most games (or the last listed)."""
    rows = [r for r in bbref.load_coaches(season) if teams.canon(r.get("team", "")) == team]
    if not rows:
        return None
    return rows[-1] if all(not _f(r.get("cur_g")) for r in rows) else max(rows, key=lambda r: _f(r.get("cur_g")) or 0)


def philosophy(coach_name: str, history: list[dict[str, Any]], pct_cache: dict[int, dict]) -> dict[str, Any]:
    seasons = [h for h in history if FIRST <= h["season"] <= LAST and h["games"] >= MIN_GAMES]
    if not seasons:
        return {"seasons": 0, "summary": f"{coach_name} has no completed NBA head-coaching season (41+ games) "
                                          f"since 2018-19, so his teams have no fingerprint yet.",
                "metrics": [], "tags": []}
    metrics = []
    for key, _table, _cols, label, high, low in METRICS:
        vals = [pct_cache[h["season"]].get(h["team"], {}).get(key) for h in seasons]
        vals = [v for v in vals if v is not None]
        if not vals:
            continue
        avg = sum(vals) / len(vals)
        metrics.append({"key": key, "label": label, "percentile": round(avg, 1), "seasons": len(vals),
                        "high": high, "low": low})
    tags = []
    for m in metrics:
        if m["percentile"] >= 75:
            tags.append({"key": m["key"], "tag": m["high"].capitalize(), "percentile": m["percentile"]})
        elif m["percentile"] <= 25:
            tags.append({"key": m["key"], "tag": m["low"].capitalize(), "percentile": m["percentile"]})
    tags.sort(key=lambda t: -abs(t["percentile"] - 50))
    clubs = []
    for h in seasons:
        if h["team"] not in clubs:
            clubs.append(h["team"])
    offense = [t["tag"].lower() for t in tags if not t["key"].startswith(("opp_", "drb"))][:3]
    defense = [t["tag"].lower() for t in tags if t["key"].startswith(("opp_", "drb"))][:3]
    names = ", ".join(teams.short(c) for c in clubs)
    span = f"{len(seasons)} season{'s' if len(seasons) != 1 else ''} with the {names} since 2018-19"
    parts = []
    if offense:
        parts.append("offense " + _join(offense))
    if defense:
        parts.append("defense " + _join(defense))
    surname = coach_name.split()[-1]
    summary = (f"{surname}'s teams, {span}: " + "; ".join(parts) + "." if parts else
               f"{surname}'s teams, {span}, sat near the league middle on every tendency measured.")
    return {"seasons": len(seasons), "clubs": clubs, "metrics": metrics, "tags": tags, "summary": summary}


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def profile_all(season: int) -> dict[str, dict[str, Any]]:
    """team -> {head_coach, season_with_club, first_season_with_club, seasons_overall, career, philosophy}."""
    hist = careers()
    pct_cache = {y: team_percentiles(y) for y in range(FIRST, LAST + 1)}
    out = {}
    for team in teams.all_abbrs():
        row = head_coach(team, season)
        if not row:
            continue
        name = row["coach"]
        history = hist.get(_norm(name), [])
        franch = int(_f(row.get("seas_num_franch")) or 0)
        out[team] = {
            "head_coach": name,
            "season_with_club": franch or None,
            "first_season_with_club": franch == 1,
            "seasons_as_head_coach": int(_f(row.get("seas_num_overall")) or 0) or None,
            "career_record": f"{int(_f(row.get('car_w')) or 0)}-{int(_f(row.get('car_l')) or 0)}",
            "career": [{k: h[k] for k in ("season_label", "team", "games", "w", "l")}
                       for h in sorted(history, key=lambda h: h["season"]) if h["season"] < season],
            "philosophy": philosophy(name, history, pct_cache),
        }
    return out
