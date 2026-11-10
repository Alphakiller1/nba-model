"""NBA's public measured zone/non-zone feed; no coverage inferred from shot mix."""
from __future__ import annotations

import json
import math
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from . import nbastats, teams

DATA = Path(__file__).resolve().parents[2] / "data" / "coverage" / "nba-zone"
URL = "https://stats.nba.com/stats/zonedefenseteamleaders"
TABLE_URL = "https://www.nba.com/inside-the-game/team/zonedefense"
MIN_POSS = 20
SIDES = ("offense", "defense")


def load(season: int, side: str) -> dict:
    path = DATA / str(season) / f"{side}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def load_public_table(season: int) -> dict:
    """Validate a dated capture of the visible NBA.com regular-season tables."""
    path = DATA / str(season) / "public-table.json"
    if not path.is_file():
        return {}
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    stamp = datetime.fromisoformat(snapshot["retrieved_at_utc"])
    if snapshot["season"] != season or snapshot["source_url"] != TABLE_URL or stamp.tzinfo is None:
        raise ValueError("Unexpected NBA coverage table identity")

    rows = {}
    for side in SIDES:
        source_rows = snapshot[side]
        if len(source_rows) != 30 or {teams.canon(row[0]) for row in source_rows} != set(teams.all_abbrs()):
            raise ValueError("NBA coverage table must contain all 30 teams")
        rows[side] = []
        for team, _label, games, total, z_share, z_ppp, z_tov, n_share, n_ppp, n_tov in source_rows:
            if not isinstance(total, int) or not isinstance(games, int) or total <= 0 or games <= 0:
                raise ValueError(f"Invalid games or possessions for {team}")
            if not all(math.isfinite(v) and v >= 0 for v in (z_share, n_share, z_ppp, n_ppp, z_tov, n_tov)):
                raise ValueError(f"Invalid published coverage rates for {team}")
            if z_share > 100 or n_share > 100 or z_tov > 100 or n_tov > 100 or abs(z_share + n_share - 100) > 0.001:
                raise ValueError(f"Coverage shares do not reconcile for {team}")
            # The printed share is rounded to 0.1 percentage points. Only
            # accept it when precisely one integer count fits the interval.
            share = round(z_share * 10)
            lower = max(0, (total * (2 * share - 1) + 1999) // 2000)
            upper = min(total, total * (2 * share + 1) // 2000)
            if lower != upper:
                raise ValueError(f"Published share leaves the zone count ambiguous for {team}")
            rows[side].append({"TEAM_ABBREVIATION": team, "GP": games, "POSS": total, "POSS_ZONE": lower,
                               "PCT_POSS_ZONE": z_share / 100, "PCT_POSS_NO_ZONE": n_share / 100,
                               "PTS_PER_POSS_ZONE": z_ppp, "PTS_PER_POSS_NO_ZONE": n_ppp,
                               "PCT_TOV_ZONE": z_tov / 100, "PCT_TOV_NO_ZONE": n_tov / 100})
    if {r["TEAM_ABBREVIATION"]: r["GP"] for r in rows["offense"]} != {
        r["TEAM_ABBREVIATION"]: r["GP"] for r in rows["defense"]
    } or any(sum(r[k] for r in rows["offense"]) != sum(r[k] for r in rows["defense"])
             for k in ("POSS", "POSS_ZONE")):
        raise ValueError("NBA offense and defense coverage tables do not reconcile")
    return {"retrieved_at_utc": snapshot["retrieved_at_utc"], "rows": rows}


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

    sources = {side: load(season, side) for side in SIDES}
    exact = {side: validate(source["payload"], season, side) if source else [] for side, source in sources.items()}
    table = load_public_table(season)
    use_table = False
    if table:
        for side in SIDES:
            current = {teams.canon(r["TEAM_ABBREVIATION"]): r["GP"] for r in exact[side]}
            if any(current.get(teams.canon(r["TEAM_ABBREVIATION"]), -1) < r["GP"] for r in table["rows"][side]):
                use_table = True
                break

    out: dict[str, dict] = {}
    for side in SIDES:
        rows = table["rows"][side] if use_table else exact[side]
        if not rows:
            continue
        stamp = table["retrieved_at_utc"] if use_table else sources[side]["retrieved_at_utc"]
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
                                  "source_url": TABLE_URL,
                                  "classification": "Measured zone / non-zone; non-zone is not relabeled man defense.",
                                  "minimum_possessions": MIN_POSS})[side] = {
                "games": row["GP"], "possessions": row["POSS"], "rows": coverage_rows,
                "retrieved_at_utc": stamp}
            if use_table:
                out[team][side]["ppp_decimals"] = 2
    return out
