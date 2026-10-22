"""Defensive scheme reads: switching, pick-and-roll coverage, rim protection, zone signature.

No public source labels a defense's coverage, so each read is measured from
what the coverage leaves behind, and every read shows the figures it rests on:

* switching       - share of a club's centers' defensive possessions spent on
                    guards, from the league's matchup data (who guarded whom)
* drop coverage   - pick-and-roll ball handlers kept in front (low turnovers
                    forced), opponents funnelled to the mid-range and kept off
                    the rim
* aggressive      - hedge/blitz signature: pick-and-roll ball handlers forced
                    into turnovers, roll men freed behind the pressure
* rim protection  - opponents' FG% at the rim and their share of shots there
* zone            - not read. The public trace of zone (few isolations faced,
                    many assisted threes conceded) flagged Oklahoma City and
                    Boston and missed Miami, the league's best-known zone team,
                    so it was dropped rather than published (2026-10-21).

Each index is a league percentile (0-100, higher = more of it). A read is
named only where the evidence is clear (75th percentile and beyond).
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from . import coaches, nbastats, teams


def _f(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _pct(values: dict[str, float]) -> dict[str, float]:
    pool = sorted(values.values())
    n = len(pool)
    return {t: round(100.0 * sum(1 for x in pool if x < v) / (n - 1), 1) for t, v in values.items()} if n > 1 else {}


def switch_rates(season: int) -> dict[str, dict[str, float]]:
    """team -> {big_on_guard, guard_on_big} shares of defensive matchup possessions."""
    rows, _ = nbastats.load(season, "matchups")
    if not rows:
        return {}
    pos: dict[int, str] = {}
    for r in nbastats.load(season, "defense_Overall")[0]:
        pos[r.get("CLOSE_DEF_PERSON_ID")] = str(r.get("PLAYER_POSITION") or "")
    team_of: dict[str, str] = {}
    for r in nbastats.load(season, "synergy_T_defensive_Transition")[0]:
        team_of[str(r.get("TEAM_ID"))] = teams.canon(r.get("TEAM_ABBREVIATION", ""))
    acc: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for r in rows:
        team = team_of.get(str(r.get("DEF_TEAM_ID")))
        p = pos.get(r.get("DEF_PLAYER_ID"), "")
        poss = _f(r.get("PARTIAL_POSS")) or 0.0
        if not team or not p:
            continue
        role = "big" if p.startswith("C") or p.endswith("C") else ("guard" if p.startswith("G") else None)
        if not role:
            continue
        if r.get("POSITION") == "TOTAL":
            acc[team][role + "_total"] += poss
        elif r.get("POSITION") == "G" and role == "big":
            acc[team]["big_on_guard"] += poss
        elif r.get("POSITION") == "C" and role == "guard":
            acc[team]["guard_on_big"] += poss
    out = {}
    for team, a in acc.items():
        if a["big_total"] > 0 and a["guard_total"] > 0:
            out[team] = {"big_on_guard": a["big_on_guard"] / a["big_total"],
                         "guard_on_big": a["guard_on_big"] / a["guard_total"]}
    return out


def _synergy(season: int, play_type: str, col: str) -> dict[str, float]:
    out = {}
    for r in nbastats.load(season, f"synergy_T_defensive_{play_type}")[0]:
        v = _f(r.get(col))
        if v is not None:
            out[teams.canon(r.get("TEAM_ABBREVIATION", ""))] = v
    return out


def _opp_shooting(year: int, col: str) -> dict[str, float]:
    from . import bbref
    out = {}
    for row in bbref.load_team_seasons(year).get("shooting-opponent", []):
        t = coaches._abbr(row.get("team"))
        v = _f(row.get(col))
        if t and v is not None:
            out[t] = v
    return out


INDEX_LABELS = {
    "switch": "Switching (Centers Guarding Guards)",
    "pnr_pressure": "Pick-And-Roll Pressure (Ball-Handler Turnovers Forced)",
    "pnr_containment": "Pick-And-Roll Containment (Ball-Handler Points Allowed, Inverted)",
    "roll_allowed": "Roll-Man Looks Allowed",
    "mid_funnel": "Opponents Funnelled To The Mid-Range",
    "rim_deterrence": "Opponents Kept Off The Rim",
    "rim_protection": "Rim Protection (Opponent FG% Within 3 Ft, Inverted)",
    "iso_faced": "Isolations Faced",
    "threes_conceded": "Threes Conceded",
    "assisted_threes": "Opponent Threes Assisted",
}


def reads(season: int) -> dict[str, dict[str, Any]]:
    """team -> {basis, indices{key: {label, percentile, value}}, reads[{scheme, strength, evidence}]}"""
    sw = switch_rates(season)
    raw: dict[str, dict[str, float]] = defaultdict(dict)
    for team, v in sw.items():
        raw["switch"][team] = v["big_on_guard"]
    for team, v in _synergy(season, "PRBallHandler", "TOV_POSS_PCT").items():
        raw["pnr_pressure"][team] = v
    for team, v in _synergy(season, "PRBallHandler", "PPP").items():
        raw["pnr_containment"][team] = -v
    for team, v in _synergy(season, "PRRollman", "POSS_PCT").items():
        raw["roll_allowed"][team] = v
    for team, v in _synergy(season, "Isolation", "POSS_PCT").items():
        raw["iso_faced"][team] = v
    mid_a, mid_b = _opp_shooting(season, "opp_pct_fga_10_16"), _opp_shooting(season, "opp_pct_fga_16_xx")
    for team in mid_a:
        if team in mid_b:
            raw["mid_funnel"][team] = mid_a[team] + mid_b[team]
    for team, v in _opp_shooting(season, "opp_pct_fga_00_03").items():
        raw["rim_deterrence"][team] = -v
    for team, v in _opp_shooting(season, "opp_fg_pct_00_03").items():
        raw["rim_protection"][team] = -v
    for team, v in _opp_shooting(season, "opp_pct_fga_fg3a").items():
        raw["threes_conceded"][team] = v
    for team, v in _opp_shooting(season, "opp_pct_ast_fg3").items():
        raw["assisted_threes"][team] = v
    pct = {k: _pct(v) for k, v in raw.items()}
    out: dict[str, dict[str, Any]] = {}
    for team in teams.all_abbrs():
        idx = {k: {"label": INDEX_LABELS[k], "percentile": pct[k][team], "value": round(abs(raw[k][team]), 4)}
               for k in INDEX_LABELS if k in pct and team in pct[k]}
        if not idx:
            continue
        p = {k: v["percentile"] for k, v in idx.items()}

        def ev(*keys):
            return [{"label": idx[k]["label"], "percentile": idx[k]["percentile"]} for k in keys if k in idx]

        found = []

        def add(name, score, keys, note):
            strength = "strong" if score >= 85 else "clear" if score >= 75 else None
            if strength:
                found.append({"scheme": name, "strength": strength, "score": round(score, 1),
                              "evidence": ev(*keys), "note": note})

        if "switch" in p:
            add("Switch-Heavy", p["switch"], ("switch", "iso_faced"),
                "Centers spend an unusual share of possessions on guards: screens are switched.")
            if p["switch"] <= 15:
                found.append({"scheme": "Bigs Stay Home", "strength": "strong" if p["switch"] <= 8 else "clear",
                              "score": round(100 - p["switch"], 1), "evidence": ev("switch"),
                              "note": "Centers rarely end up on guards: screens are not switched."})
        if {"mid_funnel", "rim_deterrence", "pnr_pressure"} <= p.keys():
            add("Drop Coverage", (p["mid_funnel"] + p["rim_deterrence"] + (100 - p["pnr_pressure"])) / 3,
                ("mid_funnel", "rim_deterrence", "pnr_pressure"),
                "Ball handlers are kept in front and conceded the mid-range while the rim is protected.")
        if {"pnr_pressure", "roll_allowed"} <= p.keys():
            add("Aggressive On The Ball (Hedge/Blitz)", (p["pnr_pressure"] * 2 + p["roll_allowed"]) / 3,
                ("pnr_pressure", "roll_allowed"),
                "Ball handlers are pressured into turnovers; the roll man is the release valve.")
        if "rim_protection" in p:
            add("Elite Rim Protection", p["rim_protection"], ("rim_protection", "rim_deterrence"),
                "Opponents finish poorly at the rim against this defense.")
        found.sort(key=lambda r: -r["score"])
        under = coaches.head_coach(team, season)
        out[team] = {"basis": f"{season - 1}-{str(season)[2:]} Season", "indices": idx, "reads": found,
                     "measured_under": (under or {}).get("coach"),
                     "not_measured": "Zone frequency: the league does not publish zone possessions, and the "
                                     "public statistical trace of zone failed a check against known zone teams."}
    return out
