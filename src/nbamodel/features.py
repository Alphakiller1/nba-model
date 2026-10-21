"""Observed season-to-date team rates and player lines, ranked against the league.

These are box-score facts, not model output: what each club has done per 100
possessions and on the four factors, for itself (``off_``) and allowed to
opponents (``def_``), each ranked among the 30 clubs. Every rate is published
as {label, value, better, rank, of, format} so a reader can always see the
figure a rank was computed from.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Sequence

from . import teams
from .ingest import Game

# key suffix, label, better ("high"/"low" for the OFFENSE side), format
RATES = (
    ("rtg", "Points per 100 possessions", "high", "num"),
    ("ppg", "Points per game", "high", "num"),
    ("efg", "Effective field-goal rate", "high", "pct"),
    ("tov", "Turnover rate", "low", "pct"),
    ("orb", "Offensive rebound rate", "high", "pct"),
    ("ftr", "Free throws made per field-goal attempt", "high", "num2"),
    ("three_rate", "Share of shots from three", "neutral", "pct"),
    ("three_pct", "Three-point rate made", "high", "pct"),
    ("two_pct", "Two-point rate made", "high", "pct"),
    ("ft_pct", "Free-throw rate made", "high", "pct"),
    ("ast_rate", "Assisted field goals", "high", "pct"),
)
DEF_LABELS = {
    "rtg": "Points allowed per 100 possessions", "ppg": "Points allowed per game",
    "efg": "Effective field-goal rate allowed", "tov": "Turnover rate forced",
    "orb": "Offensive rebound rate allowed", "ftr": "Free throws allowed per field-goal attempt",
    "three_rate": "Opponent share of shots from three", "three_pct": "Three-point rate allowed",
    "two_pct": "Two-point rate allowed", "ft_pct": "Opponent free-throw rate",
    "ast_rate": "Opponent assisted field goals",
}


def _flip(better: str) -> str:
    return {"high": "low", "low": "high"}.get(better, better)


def _side_totals(games: Sequence[Game]) -> dict[str, dict[str, float]]:
    t: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for g in games:
        poss = g.possessions()
        for me, op in (("home", "away"), ("away", "home")):
            team = getattr(g, me)
            row = t[team]
            row["games"] += 1
            row["poss"] += poss
            row["wins"] += 1 if getattr(g, f"{me}_pts") > getattr(g, f"{op}_pts") else 0
            for who, src in (("o", me), ("d", op)):
                for stat in ("pts", "fgm", "fga", "3pm", "3pa", "ftm", "fta", "oreb", "dreb", "ast", "tov"):
                    row[f"{who}_{stat}"] += getattr(g, f"{src}_{stat}")
    return t


def _rates(row: dict[str, float], who: str) -> dict[str, float]:
    other = "d" if who == "o" else "o"

    def div(a: float, b: float) -> float | None:
        return a / b if b else None

    g = lambda k: row[f"{who}_{k}"]  # noqa: E731
    two_a = g("fga") - g("3pa")
    return {
        "rtg": div(100 * g("pts"), row["poss"]),
        "ppg": div(g("pts"), row["games"]),
        "efg": div(g("fgm") + 0.5 * g("3pm"), g("fga")),
        "tov": div(g("tov"), row["poss"]),
        "orb": div(g("oreb"), g("oreb") + row[f"{other}_dreb"]),
        "ftr": div(g("ftm"), g("fga")),
        "three_rate": div(g("3pa"), g("fga")),
        "three_pct": div(g("3pm"), g("3pa")),
        "two_pct": div(g("fgm") - g("3pm"), two_a),
        "ft_pct": div(g("ftm"), g("fta")),
        "ast_rate": div(g("ast"), g("fgm")),
    }


def _rank(pool: list[float], value: float, better: str) -> int:
    if better == "low":
        return 1 + sum(1 for v in pool if v < value - 1e-12)
    return 1 + sum(1 for v in pool if v > value + 1e-12)


def team_form(games: Sequence[Game], season: int) -> dict[str, dict[str, Any]]:
    """Per-club {season, games, record, rates{off_*, def_*, net_rtg, pace}}."""
    totals = _side_totals(games)
    raw: dict[str, dict[str, float | None]] = {}
    for team, row in totals.items():
        o, d = _rates(row, "o"), _rates(row, "d")
        rates: dict[str, float | None] = {f"off_{k}": v for k, v in o.items()}
        rates.update({f"def_{k}": v for k, v in d.items()})
        rates["net_rtg"] = (o["rtg"] - d["rtg"]) if o["rtg"] is not None and d["rtg"] is not None else None
        rates["pace"] = row["poss"] / row["games"] if row["games"] else None
        raw[team] = rates
    spec: dict[str, tuple[str, str, str]] = {}
    for key, label, better, fmt in RATES:
        spec[f"off_{key}"] = (label, better, fmt)
        spec[f"def_{key}"] = (DEF_LABELS[key], _flip(better), fmt)
    spec["net_rtg"] = ("Net points per 100 possessions", "high", "signed")
    spec["pace"] = ("Possessions per game", "neutral", "num")
    out: dict[str, dict[str, Any]] = {}
    for team, rates in raw.items():
        entry: dict[str, Any] = {}
        for key, (label, better, fmt) in spec.items():
            v = rates.get(key)
            if v is None:
                continue
            pool = [r[key] for r in raw.values() if r.get(key) is not None]
            rank_by = "high" if better == "neutral" else better
            entry[key] = {"label": label, "value": round(v, 4), "better": better,
                          "rank": _rank(pool, v, rank_by), "of": len(pool), "format": fmt}
        row = totals[team]
        out[team] = {"source": "espn-box", "season": season, "games": int(row["games"]),
                     "record": f"{int(row['wins'])}-{int(row['games'] - row['wins'])}", "rates": entry}
    return out


def game_log(games: Sequence[Game], team: str, limit: int = 10) -> list[dict[str, Any]]:
    rows = []
    for g in sorted(games, key=lambda g: g.date_utc, reverse=True):
        if team not in (g.home, g.away):
            continue
        home = g.home == team
        us, them = (g.home_pts, g.away_pts) if home else (g.away_pts, g.home_pts)
        poss = g.possessions()
        rows.append({"date": g.date_utc[:10], "event_id": g.event_id, "opponent": g.away if home else g.home,
                     "home": home, "result": "W" if us > them else "L", "score": f"{us}-{them}",
                     "margin": us - them, "possessions": round(poss, 1),
                     "off_rtg": round(100 * us / poss, 1), "def_rtg": round(100 * them / poss, 1)})
        if len(rows) >= limit:
            break
    return rows


def player_lines(rows: Sequence[dict[str, str]], team_of: dict[str, str] | None = None,
                 min_games: int = 1) -> dict[str, list[dict[str, Any]]]:
    """Per-club season per-game lines for each player, most minutes first.

    ``team_of`` reassigns a player to his CURRENT club (from the roster feed),
    so last season's line follows a traded or signed player to his new team.
    """
    agg: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    names: dict[str, tuple[str, str, str]] = {}
    for r in rows:
        pid = r["athlete_id"]
        if not pid:
            continue
        mins = float(r.get("minutes") or 0)
        if mins <= 0:
            continue
        a = agg[pid]
        a["gp"] += 1
        a["gs"] += int(r.get("starter") or 0)
        a["min"] += mins
        for k in ("pts", "reb", "ast", "stl", "blk", "tov", "fgm", "fga", "tpm", "tpa", "ftm", "fta"):
            a[k] += float(r.get(k) or 0)
        names[pid] = (r.get("name", ""), r.get("position", ""), r["team"])
    by_team: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for pid, a in agg.items():
        if a["gp"] < min_games:
            continue
        name, pos, last_team = names[pid]
        team = (team_of or {}).get(pid, last_team if team_of is None else None)
        if not team:
            continue
        gp = a["gp"]
        tsa = a["fga"] + 0.44 * a["fta"]
        by_team[team].append({
            "player_id": pid, "player_name": name, "position": pos, "games": int(gp), "starts": int(a["gs"]),
            "mpg": round(a["min"] / gp, 1), "ppg": round(a["pts"] / gp, 1), "rpg": round(a["reb"] / gp, 1),
            "apg": round(a["ast"] / gp, 1), "spg": round(a["stl"] / gp, 1), "bpg": round(a["blk"] / gp, 1),
            "tpg": round(a["tov"] / gp, 1), "three_pm": round(a["tpm"] / gp, 1),
            "ts_pct": round(a["pts"] / (2 * tsa), 3) if tsa else None,
            "three_pct": round(a["tpm"] / a["tpa"], 3) if a["tpa"] else None,
            "headshot": f"https://a.espncdn.com/i/headshots/nba/players/full/{pid}.png",
            "moved": bool(team_of) and team != last_team,
        })
    for team in by_team:
        by_team[team].sort(key=lambda p: -p["mpg"] * p["games"])
    return dict(by_team)


def known_team(abbr: str) -> bool:
    return teams.canon(abbr) in teams.BY_ABBR
