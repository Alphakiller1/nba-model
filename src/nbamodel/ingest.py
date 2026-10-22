"""Season box-score cache: one row per final game, one row per player line.

A season's event ids come from the 30 club schedules (ESPN serves no
date-range scoreboard for the NBA). Each final game is read once from ESPN's
game summary, which carries the team box score, every player line and
DraftKings' closing quote, then written to ``data/games``. Finals never change,
so a cached game is never fetched again; only new finals cost a request.
"""
from __future__ import annotations

import csv
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, Iterable

from . import espn, teams

DATA = Path(__file__).resolve().parents[2] / "data" / "games"


@dataclass
class Game:
    event_id: str
    season: int
    date_utc: str
    home: str
    away: str
    neutral: int
    home_pts: int
    away_pts: int
    home_fgm: int
    home_fga: int
    home_3pm: int
    home_3pa: int
    home_ftm: int
    home_fta: int
    home_oreb: int
    home_dreb: int
    home_ast: int
    home_tov: int
    away_fgm: int
    away_fga: int
    away_3pm: int
    away_3pa: int
    away_ftm: int
    away_fta: int
    away_oreb: int
    away_dreb: int
    away_ast: int
    away_tov: int
    # DraftKings closing quote from ESPN's pick center. Spread is the HOME
    # handicap (home -4.5 means the home side is favoured by 4.5); the expected
    # home margin is its negative. Blank when ESPN carried no quote.
    dk_spread: str = ""
    dk_total: str = ""
    dk_home_ml: str = ""
    dk_away_ml: str = ""
    venue: str = ""

    def possessions(self) -> float:
        """Average of the two clubs' possession estimates."""
        def est(fga: int, fta: int, oreb: int, tov: int) -> float:
            return fga - oreb + tov + 0.44 * fta
        return 0.5 * (est(self.home_fga, self.home_fta, self.home_oreb, self.home_tov)
                      + est(self.away_fga, self.away_fta, self.away_oreb, self.away_tov))


@dataclass
class PlayerLine:
    event_id: str
    team: str
    athlete_id: str
    name: str
    position: str
    starter: int
    minutes: float
    pts: int
    fgm: int
    fga: int
    tpm: int
    tpa: int
    ftm: int
    fta: int
    reb: int
    ast: int
    tov: int
    stl: int
    blk: int
    plus_minus: int


GAME_FIELDS = [f.name for f in fields(Game)]
PLAYER_FIELDS = [f.name for f in fields(PlayerLine)]


def _int(v: Any) -> int:
    try:
        return int(float(str(v).replace("+", "")))
    except (TypeError, ValueError):
        return 0


def _pair(v: str) -> tuple[int, int]:
    made, _, att = str(v or "0-0").partition("-")
    return _int(made), _int(att)


def _team_box(team: dict[str, Any]) -> dict[str, int]:
    stats = {s.get("name"): s.get("displayValue") for s in team.get("statistics", [])}
    fgm, fga = _pair(stats.get("fieldGoalsMade-fieldGoalsAttempted"))
    tpm, tpa = _pair(stats.get("threePointFieldGoalsMade-threePointFieldGoalsAttempted"))
    ftm, fta = _pair(stats.get("freeThrowsMade-freeThrowsAttempted"))
    # totalTurnovers includes team turnovers (shot clock, 8-second); those are
    # possessions ended too.
    tov = stats.get("totalTurnovers", stats.get("turnovers"))
    return {"fgm": fgm, "fga": fga, "3pm": tpm, "3pa": tpa, "ftm": ftm, "fta": fta,
            "oreb": _int(stats.get("offensiveRebounds")), "dreb": _int(stats.get("defensiveRebounds")),
            "ast": _int(stats.get("assists")), "tov": _int(tov)}


def _dk(pick: list[dict[str, Any]]) -> dict[str, str]:
    for quote in pick or []:
        # ESPN spells it "Draft Kings" to some callers (GitHub's runners).
        if str((quote.get("provider") or {}).get("name", "")).lower().replace(" ", "") != "draftkings":
            continue
        def s(v: Any) -> str:
            return "" if v is None else str(v)
        return {"dk_spread": s(quote.get("spread")), "dk_total": s(quote.get("overUnder")),
                "dk_home_ml": s((quote.get("homeTeamOdds") or {}).get("moneyLine")),
                "dk_away_ml": s((quote.get("awayTeamOdds") or {}).get("moneyLine"))}
    return {}


def parse_summary(event_id: str, season: int, data: dict[str, Any]) -> tuple[Game, list[PlayerLine]] | None:
    """A final regular-season game, or None for anything else."""
    comp = ((data.get("header") or {}).get("competitions") or [{}])[0]
    if not ((comp.get("status") or {}).get("type") or {}).get("completed"):
        return None
    sides: dict[str, dict[str, Any]] = {}
    for c in comp.get("competitors", []):
        sides[c.get("homeAway")] = c
    if set(sides) != {"home", "away"}:
        return None
    abbr = {k: teams.canon(v["team"]["abbreviation"]) for k, v in sides.items()}
    if any(a not in teams.BY_ABBR for a in abbr.values()):
        return None  # All-Star or exhibition sides
    box = {}
    for t in (data.get("boxscore") or {}).get("teams", []):
        box[teams.canon(t["team"]["abbreviation"])] = _team_box(t)
    if set(box) != set(abbr.values()):
        return None
    h, a = box[abbr["home"]], box[abbr["away"]]
    if h["fga"] == 0 or a["fga"] == 0:
        return None
    venue = (((data.get("gameInfo") or {}).get("venue")) or {}).get("fullName", "")
    game = Game(
        event_id=event_id, season=season, date_utc=comp.get("date", ""),
        home=abbr["home"], away=abbr["away"], neutral=1 if comp.get("neutralSite") else 0,
        home_pts=_int(sides["home"].get("score")), away_pts=_int(sides["away"].get("score")),
        **{f"home_{k}": v for k, v in h.items()}, **{f"away_{k}": v for k, v in a.items()},
        **_dk(data.get("pickcenter") or []), venue=venue,
    )
    players: list[PlayerLine] = []
    for block in (data.get("boxscore") or {}).get("players", []):
        team = teams.canon(block["team"]["abbreviation"])
        for stat in block.get("statistics", [])[:1]:
            keys = stat.get("keys", [])
            for ath in stat.get("athletes", []):
                if ath.get("didNotPlay") or not ath.get("stats"):
                    continue
                row = dict(zip(keys, ath["stats"]))
                fgm, fga = _pair(row.get("fieldGoalsMade-fieldGoalsAttempted"))
                tpm, tpa = _pair(row.get("threePointFieldGoalsMade-threePointFieldGoalsAttempted"))
                ftm, fta = _pair(row.get("freeThrowsMade-freeThrowsAttempted"))
                person = ath.get("athlete") or {}
                try:
                    minutes = float(row.get("minutes") or 0)
                except ValueError:
                    minutes = 0.0
                players.append(PlayerLine(
                    event_id=event_id, team=team, athlete_id=str(person.get("id", "")),
                    name=person.get("displayName", ""),
                    position=(person.get("position") or {}).get("abbreviation", ""),
                    starter=1 if ath.get("starter") else 0, minutes=minutes,
                    pts=_int(row.get("points")), fgm=fgm, fga=fga, tpm=tpm, tpa=tpa, ftm=ftm, fta=fta,
                    reb=_int(row.get("rebounds")), ast=_int(row.get("assists")),
                    tov=_int(row.get("turnovers")), stl=_int(row.get("steals")),
                    blk=_int(row.get("blocks")), plus_minus=_int(row.get("plusMinus")),
                ))
    return game, players


def season_events(season: int) -> list[dict[str, Any]]:
    """Every regular-season event on the 30 schedules: id, date, state."""
    seen: dict[str, dict[str, Any]] = {}
    for tid in teams.BY_ID:
        data = espn.team_schedule(tid, season)
        for ev in data.get("events", []):
            comp = (ev.get("competitions") or [{}])[0]
            state = ((comp.get("status") or {}).get("type") or {})
            seen[str(ev["id"])] = {"id": str(ev["id"]), "date": ev.get("date", ""),
                                   "completed": bool(state.get("completed"))}
    return sorted(seen.values(), key=lambda e: (e["date"], e["id"]))


def _path(season: int, kind: str) -> Path:
    return DATA / f"{season}_{kind}.csv"


def _read(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _write(path: Path, names: list[str], rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=names, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    tmp.replace(path)


def _coerce(row: dict[str, str]) -> Game:
    kw: dict[str, Any] = {}
    for f in fields(Game):
        v = row.get(f.name, "")
        kw[f.name] = int(v) if f.type in (int, "int") else v
    return Game(**kw)


def load_games(season: int) -> list[Game]:
    return sorted((_coerce(r) for r in _read(_path(season, "games"))), key=lambda g: g.date_utc)


def load_players(season: int) -> list[dict[str, str]]:
    return _read(_path(season, "players"))


def refresh(season: int, *, workers: int = 8, log=print) -> tuple[int, int]:
    """Fetch finals not yet cached. Returns (new games, cached total)."""
    games = {r["event_id"]: r for r in _read(_path(season, "games"))}
    players = _read(_path(season, "players"))
    events = season_events(season)
    todo = [e["id"] for e in events if e["completed"] and e["id"] not in games]
    log(f"season {season}: {len(events)} scheduled, {len(games)} cached, {len(todo)} to fetch")
    if not todo:
        return 0, len(games)

    def one(eid: str):
        try:
            return eid, parse_summary(eid, season, espn.summary(eid))
        except espn.SourceError as exc:
            log(f"  skip {eid}: {exc}")
            return eid, None

    added = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for eid, parsed in pool.map(one, todo):
            if parsed is None:
                continue
            game, lines = parsed
            games[eid] = {k: getattr(game, k) for k in GAME_FIELDS}
            players.extend({k: getattr(p, k) for k in PLAYER_FIELDS} for p in lines)
            added += 1
    _write(_path(season, "games"), GAME_FIELDS,
           sorted(games.values(), key=lambda r: (r["date_utc"], r["event_id"])))
    _write(_path(season, "players"), PLAYER_FIELDS, players)
    log(f"season {season}: +{added} games, {len(games)} cached")
    return added, len(games)
