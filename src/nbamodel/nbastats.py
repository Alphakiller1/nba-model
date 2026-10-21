"""stats.nba.com: play types, tracking, defense by zone, shot locations, bio, coaches.

The league's own stats site serves Synergy play types (pick-and-roll, isolation,
post-up, transition ...), Second Spectrum tracking (speed, distance, drives,
catch-and-shoot), defender dashboards (opponent FG% by distance against the
defender) and shot locations by zone. It answers browsers, so requests carry a
browser's headers.

It is known to refuse some cloud networks (CI runners). Every pull is written to
``data/nbastats/{season}/`` with its fetch time; a build that cannot reach the
site reads the last snapshot and says how old it is. Nothing here is computed:
these are the league's published figures.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE = "https://stats.nba.com/stats/"
DATA = Path(__file__).resolve().parents[2] / "data" / "nbastats"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/128.0 Safari/537.36",
    "Referer": "https://www.nba.com/", "Origin": "https://www.nba.com",
    "Accept": "application/json, text/plain, */*", "Accept-Language": "en-US,en;q=0.9",
    "x-nba-stats-origin": "stats", "x-nba-stats-token": "true",
}

PLAY_TYPES = ("Isolation", "Transition", "PRBallHandler", "PRRollman", "Postup", "Spotup", "Handoff", "Cut",
              "OffScreen", "OffRebound", "Misc")
PLAY_TYPE_LABEL = {
    "Isolation": "Isolation", "Transition": "Transition", "PRBallHandler": "Pick-And-Roll Ball Handler",
    "PRRollman": "Pick-And-Roll Roll Man", "Postup": "Post-Up", "Spotup": "Spot-Up", "Handoff": "Handoff",
    "Cut": "Cut", "OffScreen": "Off Screen", "OffRebound": "Putbacks", "Misc": "Miscellaneous",
}
TRACKING = ("SpeedDistance", "Drives", "CatchShoot", "PullUpShot", "PostTouch", "Rebounding")
DEFENSE = ("Overall", "3 Pointers", "2 Pointers", "Less Than 6Ft", "Less Than 10Ft", "Greater Than 15Ft")


class Blocked(RuntimeError):
    """stats.nba.com did not answer (refused, timed out, or returned no table)."""


def season_label(season: int) -> str:
    """ESPN season year (2026 = 2025-26) to the league's 'YYYY-YY'."""
    return f"{season - 1}-{str(season)[2:]}"


def get(endpoint: str, params: dict[str, Any], *, attempts: int = 3, timeout: float = 30.0) -> list[dict]:
    url = BASE + endpoint + "?" + urllib.parse.urlencode(params)
    last: Exception | None = None
    for i in range(attempts):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            sets = data.get("resultSets") or data.get("resultSet") or []
            if isinstance(sets, dict):  # shot locations send one set, not a list
                sets = [sets]
            out = []
            for rs in sets:
                hdr = rs.get("headers")
                if isinstance(hdr, list) and hdr and isinstance(hdr[0], dict):  # shot locations: two-row header
                    names = []
                    zones = hdr[0]["columnNames"]
                    span = hdr[0].get("columnSpan", 1)
                    skip = hdr[0].get("columnsToSkip", 0)
                    for j, n in enumerate(hdr[1]["columnNames"]):
                        z = (j - skip) // 3 if j >= skip else None
                        names.append(f"{zones[z]}|{n}" if z is not None and z < len(zones) else n)
                    _ = span
                    hdr = names
                out.extend({"_set": rs.get("name", ""), **dict(zip(hdr, row))} for row in rs.get("rowSet", []))
            return out
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, KeyError) as exc:
            last = exc
            time.sleep(1.5 * (i + 1))
    raise Blocked(f"{endpoint}: {last}")


def _common(season: int) -> dict[str, Any]:
    return {"LeagueID": "00", "Season": season_label(season), "SeasonType": "Regular Season", "PerMode": "PerGame"}


def synergy(season: int, who: str, grouping: str, play_type: str) -> list[dict]:
    return get("synergyplaytypes", {"LeagueID": "00", "PerMode": "PerGame", "PlayType": play_type,
                                    "PlayerOrTeam": who, "SeasonType": "Regular Season",
                                    "SeasonYear": season_label(season), "TypeGrouping": grouping})


def tracking(season: int, who: str, measure: str) -> list[dict]:
    return get("leaguedashptstats", {**_common(season), "PlayerOrTeam": who, "PtMeasureType": measure,
                                     "LastNGames": 0, "Month": 0, "OpponentTeamID": 0, "PORound": 0,
                                     "TeamID": 0})


def defense(season: int, category: str) -> list[dict]:
    return get("leaguedashptdefend", {**_common(season), "DefenseCategory": category, "LastNGames": 0,
                                      "Month": 0, "OpponentTeamID": 0, "PORound": 0, "Period": 0, "TeamID": 0})


def shot_locations(season: int, who: str, measure: str = "Base") -> list[dict]:
    endpoint = "leaguedashteamshotlocations" if who == "Team" else "leaguedashplayershotlocations"
    return get(endpoint, {**_common(season), "DistanceRange": "By Zone", "MeasureType": measure,
                          "LastNGames": 0, "Month": 0, "OpponentTeamID": 0, "PORound": 0, "Period": 0,
                          "TeamID": 0, "PaceAdjust": "N", "PlusMinus": "N", "Rank": "N"})


def bio(season: int) -> list[dict]:
    return get("leaguedashplayerbiostats", {**_common(season), "LastNGames": 0, "Month": 0,
                                            "OpponentTeamID": 0, "PORound": 0, "TeamID": 0})


def roster(season: int, team_id: str) -> list[dict]:
    return get("commonteamroster", {"LeagueID": "00", "Season": season_label(season), "TeamID": team_id})


def teams(season: int) -> list[dict]:
    return get("leaguedashteamstats", {**_common(season), "MeasureType": "Base", "LastNGames": 0, "Month": 0,
                                       "OpponentTeamID": 0, "PORound": 0, "Period": 0, "TeamID": 0,
                                       "PaceAdjust": "N", "PlusMinus": "N", "Rank": "N"})


def path(season: int, name: str) -> Path:
    return DATA / str(season) / f"{name}.json"


def save(season: int, name: str, rows: list[dict]) -> None:
    p = path(season, name)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"fetched_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                             "rows": rows}, separators=(",", ":")) + "\n", encoding="utf-8")


def load(season: int, name: str) -> tuple[list[dict], str | None]:
    p = path(season, name)
    if not p.exists():
        return [], None
    d = json.loads(p.read_text(encoding="utf-8"))
    return d.get("rows", []), d.get("fetched_at_utc")


def reachable(season: int) -> bool:
    """One quick request: cloud runners the site refuses tend to hang, not refuse."""
    try:
        get("synergyplaytypes", {"LeagueID": "00", "PerMode": "PerGame", "PlayType": "Transition",
                                 "PlayerOrTeam": "T", "SeasonType": "Regular Season",
                                 "SeasonYear": season_label(season), "TypeGrouping": "offensive"},
            attempts=1, timeout=10.0)
        return True
    except Blocked:
        return False


def refresh(season: int, log=print, pause: float = 0.6) -> dict[str, int]:
    """Pull every table for a season; a blocked pull keeps the previous snapshot."""
    if not reachable(season):
        log(f"nbastats {season}: stats.nba.com not reachable from here; keeping the snapshot")
        return {}
    jobs: list[tuple[str, Any]] = []
    for who in ("T", "P"):
        for grouping in ("offensive", "defensive"):
            if who == "P" and grouping == "defensive":
                continue
            for pt in PLAY_TYPES:
                jobs.append((f"synergy_{who}_{grouping}_{pt}",
                             lambda w=who, g=grouping, t=pt: synergy(season, w, g, t)))
    for who in ("Team", "Player"):
        for m in TRACKING:
            jobs.append((f"tracking_{who}_{m}", lambda w=who, m=m: tracking(season, w, m)))
    for c in DEFENSE:
        jobs.append((f"defense_{c.replace(' ', '_')}", lambda c=c: defense(season, c)))
    jobs.append(("shots_Team_Base", lambda: shot_locations(season, "Team", "Base")))
    jobs.append(("shots_Team_Opponent", lambda: shot_locations(season, "Team", "Opponent")))
    jobs.append(("shots_Player_Base", lambda: shot_locations(season, "Player", "Base")))
    jobs.append(("bio", lambda: bio(season)))
    jobs.append(("teams", lambda: teams(season)))
    got = {}
    misses = 0
    for name, fn in jobs:
        try:
            rows = fn()
            misses = 0
            if rows:
                save(season, name, rows)
                got[name] = len(rows)
        except Blocked as exc:
            misses += 1
            log(f"  nbastats {season} {name}: {exc}")
            if misses >= 3:  # the site has stopped answering; keep every other snapshot as it is
                log(f"nbastats {season}: three refusals in a row; stopping")
                return got
        time.sleep(pause)
    try:
        team_rows = teams(season) if "teams" not in got else load(season, "teams")[0]
        coaches = []
        for t in team_rows:
            for r in roster(season, str(t["TEAM_ID"])):
                if r.get("_set") == "Coaches":
                    coaches.append(r)
            time.sleep(pause)
        if coaches:
            save(season, "coaches", coaches)
            got["coaches"] = len(coaches)
    except Blocked as exc:
        log(f"  nbastats {season} coaches: {exc}")
    log(f"nbastats {season}: {len(got)} tables")
    return got
