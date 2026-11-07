"""NBA's public measured zone/non-zone feed; no coverage inferred from shot mix."""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from . import nbastats, teams

DATA = Path(__file__).resolve().parents[2] / "data" / "coverage" / "nba-zone"
URL = "https://stats.nba.com/stats/zonedefenseteamleaders"
MIN_POSS = 20


def load(season: int, side: str) -> dict:
    path = DATA / str(season) / f"{side}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def validate(payload: dict, season: int, side: str) -> list[dict]:
    rows = payload.get("teamZoneDefense", [])
    if payload.get("seasonYear") != f"{season - 1}-{str(season)[-2:]}":
        raise ValueError("Coverage source returned another season")
    if str(payload.get("typeGrouping", "")).lower() != side:
        raise ValueError("Coverage source returned another direction")
    if len(rows) != 30 or {teams.canon(r["TEAM_ABBREVIATION"]) for r in rows} != set(teams.all_abbrs()):
        raise ValueError("Coverage source is missing NBA teams")
    for row in rows:
        total, zone = row["POSS"], row["POSS_ZONE"]
        if not 0 <= zone <= total or row["GP"] < 0:
            raise ValueError("Invalid coverage sample")
        if abs(row["PCT_POSS_ZONE"] + row["PCT_POSS_NO_ZONE"] - 1) > .002:
            raise ValueError("Coverage shares do not partition the sample")
        if zone and abs(row["POSS_ZONE_PTS"] / zone - row["PTS_PER_POSS_ZONE"]) > .0011:
            raise ValueError("Zone PPP does not reconcile to points and possessions")
    return rows


def refresh(season: int, *, force: bool = False) -> dict:
    """Fetch both directions, validate, then publish the paired snapshot."""
    now = datetime.now(timezone.utc)
    pending = []
    for side in ("offense", "defense"):
        old = load(season, side)
        if old and not force:
            elapsed = now - datetime.fromisoformat(old["retrieved_at_utc"])
            if elapsed.total_seconds() < 6 * 3600:
                continue
        params = {"LeagueID": "00", "Season": f"{season - 1}-{str(season)[-2:]}",
                  "SeasonType": "Regular Season", "type": side}
        url = URL + "?" + urllib.parse.urlencode(params)
        with urllib.request.urlopen(urllib.request.Request(url, headers=nbastats.HEADERS), timeout=25) as response:
            payload = json.loads(response.read())
        rows = validate(payload, season, side)
        previous = {r["TEAM_ID"]: r for r in old.get("payload", {}).get("teamZoneDefense", [])}
        if any(r["GP"] < previous.get(r["TEAM_ID"], {}).get("GP", 0) for r in rows):
            raise ValueError("Coverage game count regressed; last snapshot retained")
        pending.append((side, {"source_url": url, "retrieved_at_utc": now.isoformat(), "payload": payload}))
    for side, value in pending:
        path = DATA / str(season) / f"{side}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(value, separators=(",", ":")) + "\n", encoding="utf-8")
        temp.replace(path)
    return {side: len(load(season, side).get("payload", {}).get("teamZoneDefense", []))
            for side in ("offense", "defense")}


def team_contexts(season: int) -> dict[str, dict]:
    from .profiles import ranked

    out: dict[str, dict] = {}
    for side in ("offense", "defense"):
        source = load(season, side)
        if not source:
            continue
        rows = validate(source["payload"], season, side)
        groups = {"zone": ("ZONE", "POSS_ZONE"), "non_zone": ("NO_ZONE", None)}
        ranked_ppp = {}
        for key, (suffix, count_field) in groups.items():
            values = {}
            for row in rows:
                count = row[count_field] if count_field else row["POSS"] - row["POSS_ZONE"]
                if count >= MIN_POSS:
                    values[teams.canon(row["TEAM_ABBREVIATION"])] = row["PTS_PER_POSS_" + suffix]
            ranked_ppp[key] = ranked(values, "Points per possession" + (" allowed" if side == "defense" else ""),
                                     "low" if side == "defense" else "high", "num2")
        for row in rows:
            team = teams.canon(row["TEAM_ABBREVIATION"])
            coverage_rows = []
            for key, (suffix, count_field) in groups.items():
                count = row[count_field] if count_field else row["POSS"] - row["POSS_ZONE"]
                coverage_rows.append({"id": key, "label": "Zone" if key == "zone" else "Non-zone",
                                      "possessions": count, "frequency": row["PCT_POSS_" + suffix],
                                      "ppp": row["PTS_PER_POSS_" + suffix] if count else None,
                                      "turnover_rate": row["PCT_TOV_" + suffix] if count else None,
                                      "ppp_grade": ranked_ppp[key].get(team), "sample_qualified": count >= MIN_POSS})
            out.setdefault(team, {"source": "NBA Inside the Game", "season": season,
                                  "source_url": "https://www.nba.com/inside-the-game/team/zonedefense",
                                  "classification": "Measured zone / non-zone; non-zone is not relabeled man defense.",
                                  "minimum_possessions": MIN_POSS})[side] = {
                "games": row["GP"], "possessions": row["POSS"], "rows": coverage_rows,
                "retrieved_at_utc": source["retrieved_at_utc"]}
    return out
