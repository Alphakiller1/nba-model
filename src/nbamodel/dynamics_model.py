"""NBA adapters for private model risk, pace, personnel and tactical analysis."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from . import bulk, coverage, dynamics, ingest, nbastats, teams, tracking_tactics

ET = ZoneInfo("America/New_York")


def observations(seasons: tuple[int, ...]) -> list[dict]:
    rows = []
    for year in seasons:
        lines = ingest.load_players(year)
        # Player minutes provide an independent OT-duration check. We only use
        # a game's duration when summed minutes reconcile to 240 + 25*OT.
        minutes = defaultdict(float)
        returning = defaultdict(float)
        prior_roster = defaultdict(set)
        for p in ingest.load_players(year - 1):
            if float(p["minutes"] or 0) > 0:
                prior_roster[p["team"]].add(p["athlete_id"])
        for p in lines:
            minutes[(p["event_id"], p["team"])] += float(p["minutes"] or 0)
            if p["athlete_id"] in prior_roster[p["team"]]:
                returning[(p["event_id"], p["team"])] += float(p["minutes"] or 0)
        for g in ingest.load_games(year):
            stamp = dynamics.utc(g.date_utc)
            next_day = datetime.combine(stamp.astimezone(ET).date() + timedelta(days=1), time(), tzinfo=ET)
            available = max(next_day, stamp + timedelta(hours=6)).isoformat()
            duration = None
            hmin, amin = minutes[(g.event_id, g.home)], minutes[(g.event_id, g.away)]
            ot = max(0, round(((hmin + amin) / 2 - 240) / 25))
            target = 240 + ot * 25
            if abs(hmin - target) <= 3 and abs(amin - target) <= 3:
                duration = 48 + 5 * ot
            for me, other in (("home", "away"), ("away", "home")):
                row = {
                    "game_id": g.event_id,
                    "season": year,
                    "date": g.date_utc,
                    "available_at": available,
                    "team": getattr(g, me),
                    "opponent": getattr(g, other),
                    "home": me == "home",
                    "possessions": g.possessions(),
                    "duration_minutes": duration,
                    "known_exposure": minutes[(g.event_id, getattr(g, me))],
                    "returning_exposure": returning[(g.event_id, getattr(g, me))],
                }
                for prefix, side in (("", me), ("d_", other)):
                    for field, source in (
                        ("pts", "pts"),
                        ("fgm", "fgm"),
                        ("fga", "fga"),
                        ("tpm", "3pm"),
                        ("tpa", "3pa"),
                        ("ftm", "ftm"),
                        ("fta", "fta"),
                        ("tov", "tov"),
                        ("orb", "oreb"),
                        ("drb", "dreb"),
                    ):
                        row[prefix + field] = getattr(g, side + "_" + source)
                rows.append(row)
    return rows


def personnel_rows(season: int, boxes: list[dict]) -> tuple[list[dict], dict]:
    games = {g.event_id: g for g in ingest.load_games(season)}
    availability = {r["game_id"]: r["available_at"] for r in boxes if r["season"] == season}
    # Listed positions are fixed labels, not outcome-based or current-roster assignments.
    roles = {}
    for p in ingest.load_players(season):
        value = str(p["position"]).upper()
        roles[(p["event_id"], p["athlete_id"])] = (
            "C" if "C" in value else "G" if "G" in value else "F" if "F" in value else None
        )

    def package(raw, event):
        ids = raw.split("|")
        values = [roles.get((event, pid)) for pid in ids]
        if len(set(ids)) != 5 or not all(values):
            return None
        return "-".join(str(values.count(k)) + k for k in ("G", "F", "C"))

    records, omitted = [], 0
    for s in bulk.load_stints(season):
        g = games.get(s["event_id"])
        if not g:
            continue
        h, a = package(s["home_players"], s["event_id"]), package(s["away_players"], s["event_id"])
        if not h or not a:
            omitted += 1
            continue
        for me, other, own, op in (("home", "away", h, a), ("away", "home", a, h)):
            poss = float(s[me + "_fga"]) - float(s[me + "_oreb"]) + float(s[me + "_tov"]) + 0.44 * float(s[me + "_fta"])
            if poss <= 0:
                continue
            records.append(
                {
                    "game_id": g.event_id,
                    "season": season,
                    "date": g.date_utc,
                    "available_at": availability[g.event_id],
                    "team": getattr(g, me),
                    "opponent": getattr(g, other),
                    "own_personnel": own,
                    "opponent_personnel": op,
                    "possessions": poss,
                    "points": float(s[me + "_pts"]),
                }
            )
    return records, {
        "excluded_stints_unknown_personnel": omitted,
        "rows": len(records),
        "denominator": "Estimated FGA-ORB+TOV+0.44*FTA, not exact possession counts",
    }


def playtypes(season: int, as_of: str) -> dict:
    out = defaultdict(list)
    for direction, side in (("offensive", "offense"), ("defensive", "defense")):
        for kind in nbastats.PLAY_TYPES:
            rows, received = nbastats.load(season, f"synergy_T_{direction}_{kind}")
            if not received or dynamics.utc(received) > dynamics.utc(as_of):
                continue
            for r in rows:
                team = teams.canon(r.get("TEAM_ABBREVIATION") or r.get("TEAM_NAME", ""))
                if team not in teams.all_abbrs():
                    continue
                games = float(r.get("GP") or 0)
                sample = float(r.get("POSS") or 0) * games  # cached endpoint is PerGame
                out[team].append(
                    {
                        "side": side,
                        "playtype": kind,
                        "ppp": r.get("PPP"),
                        "frequency": r.get("POSS_PCT"),
                        "possessions_per_game": r.get("POSS"),
                        "estimated_total_possessions": sample,
                        "games": games,
                        "sample_qualified": sample >= 100 and games >= 5,
                        "retrieved_at_utc": received,
                        "policy": "Provider play type; not a defensive coverage classification.",
                    }
                )
    return dict(out)


def build(season: int, as_of: str | None = None, include_tactics: bool = True) -> dict:
    as_of = as_of or datetime.now(timezone.utc).isoformat()
    raw = observations((season - 2, season - 1, season))
    windows = {}
    for year in (season - 1, season):
        bank = dynamics.build(raw, year, as_of, 48)
        if include_tactics:
            forms, quality = personnel_rows(year, raw)
            packages = dynamics.personnel(forms, as_of)
            detected = tracking_tactics.contexts(year, as_of)
            measured = {}
            if all(
                coverage.load(year, side)
                and dynamics.utc(coverage.load(year, side)["retrieved_at_utc"]) <= dynamics.utc(as_of)
                for side in ("offense", "defense")
            ):
                measured = coverage.team_contexts(year)
            types = playtypes(year, as_of)
            for team, profile in bank.items():
                profile["personnel_formations"] = packages.get(team, [])
                profile["personnel_quality"] = quality
                profile["tracking_tactics"] = detected.get(team)
                profile["measured_zone_coverage"] = measured.get(team)
                profile["playtype_tendencies"] = types.get(team, [])
        windows[str(year)] = bank
    return {
        "schema": "nba-private-dynamics/1",
        "as_of": as_of,
        "seasons": windows,
        "availability_policy": "Prior local game day only, plus a conservative six-hour finalization window.",
        "tracking_policy": "Experimental labels, receipt-time eligible, zero forecast weight until validated.",
    }


def forecast_diagnostics(away: dict, home: dict, base: dict) -> dict:
    context = dynamics.matchup(away, home)
    if context["state"] != "available":
        return context
    context["away_personnel_matchup"] = dynamics.personnel_matchup(away, home)
    context["home_personnel_matchup"] = dynamics.personnel_matchup(home, away)
    for side, offense, defense in (("away", away, home), ("home", home, away)):
        own = (offense.get("tracking_tactics") or {}).get("rows", [])
        opposing = (defense.get("tracking_tactics") or {}).get("rows", [])
        cells = []
        for dr in opposing:
            if dr["side"] != "defense":
                continue
            attack = next(
                (r for r in own if r["side"] == "offense" and (r["kind"], r["label"]) == (dr["kind"], dr["label"])),
                None,
            )
            cells.append(
                {
                    "kind": dr["kind"],
                    "label": dr["label"],
                    "offense": attack,
                    "opponent_defense": dr,
                    "sample_qualified": bool(attack and attack["sample_qualified"] and dr["sample_qualified"]),
                    "label_quality": "experimental",
                    "forecast_weight": 0.0,
                }
            )
        context[side + "_detected_tactical_matchup"] = cells
        measured_offense = (offense.get("measured_zone_coverage") or {}).get("offense", {}).get("rows", [])
        measured_defense = (defense.get("measured_zone_coverage") or {}).get("defense", {}).get("rows", [])
        context[side + "_measured_zone_matchup"] = [
            {
                "label": dr["label"],
                "offense_against": next((r for r in measured_offense if r["label"] == dr["label"]), None),
                "opponent_defense": dr,
                "forecast_weight": 0.0,
            }
            for dr in measured_defense
        ]
        context[side + "_playtype_tendencies"] = offense.get("playtype_tendencies", [])
    a, h = dynamics.risk_window(away), dynamics.risk_window(home)
    n = context["pace_center"]
    ashift = n / 100 * (a.get("offense_shift_pp100", 0) - h.get("defense_prevention_shift_pp100", 0))
    hshift = n / 100 * (h.get("offense_shift_pp100", 0) - a.get("defense_prevention_shift_pp100", 0))
    context["shooting_scenario"] = {
        "away_shift": ashift,
        "home_shift": hshift,
        "away_points": base["away_points"] + ashift,
        "home_points": base["home_points"] + hshift,
        "forecast_weight": 0.0,
        "status": "Conditional reversion scenario; not a score adjustment.",
    }
    return context
