"""Date-censored basketball process, pace and shooting reversion model.

Inputs are one team's box score per completed game. All signs in the shooting
component describe the team's net scoring perspective. Pressure is a posterior
change in points/100, not a calibrated probability of future improvement.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timezone
from statistics import mean, pstdev

from scipy.stats import beta as beta_distribution

VERSION = "basketball-dynamics/1"
SHOTS = (("two", 2.0, 100.0), ("three", 3.0, 150.0), ("ft", 1.0, 80.0))


def utc(value: str) -> datetime:
    value = str(value).replace("Z", "+00:00")
    result = datetime.fromisoformat(value)
    return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result.astimezone(timezone.utc)


def before(rows: list[dict], as_of: str, season: int | None = None) -> list[dict]:
    cutoff = utc(as_of)
    return sorted(
        (
            r
            for r in rows
            if utc(r.get("available_at", r["date"])) < cutoff and (season is None or r["season"] == season)
        ),
        key=lambda r: (r["date"], str(r["game_id"])),
    )


def ratio(a: float, b: float) -> float | None:
    return a / b if b > 0 else None


def tone(change: float | None, qualified: bool = True) -> str:
    if change is None or not qualified:
        return "unknown"
    return "favorable" if change > 1e-9 else "unfavorable" if change < -1e-9 else "neutral"


def totals(rows: list[dict], defense: bool = False) -> dict:
    if isinstance(rows, dict):
        return rows
    out = defaultdict(float)
    for key in ("possessions", "pts", "fgm", "fga", "tpm", "tpa", "ftm", "fta", "tov", "orb", "drb", "opp_drb"):
        out[key] = 0.0
    prefix = "d_" if defense else ""
    for r in rows:
        out["possessions"] += r["possessions"]
        for key in ("pts", "fgm", "fga", "tpm", "tpa", "ftm", "fta", "tov", "orb", "drb"):
            out[key] += r[prefix + key]
        out["opp_drb"] += r["drb" if defense else "d_drb"]
    out["games"] = len(rows)
    return dict(out)


def rates(rows: list[dict], defense: bool = False) -> dict:
    t = defaultdict(float, totals(rows, defense))
    return {
        "pp100": ratio(100 * t["pts"], t["possessions"]),
        "efg": ratio(t["fgm"] + 0.5 * t["tpm"], t["fga"]),
        "turnover_rate": ratio(t["tov"], t["possessions"]),
        "off_rebound_rate": ratio(t["orb"], t["orb"] + t["opp_drb"]),
        "free_throw_attempt_rate": ratio(t["fta"], t["fga"]),
        "three_attempt_share": ratio(t["tpa"], t["fga"]),
    }


def shot_counts(t: dict, kind: str) -> tuple[float, float]:
    if kind == "two":
        return t["fgm"] - t["tpm"], t["fga"] - t["tpa"]
    return (t["tpm"], t["tpa"]) if kind == "three" else (t["ftm"], t["fta"])


def posterior(made: float, attempts: float, baseline: float, strength: float) -> dict:
    if not (0 <= made <= attempts and 0 < baseline < 1 and strength > 0):
        raise ValueError("Invalid binomial evidence")
    a, b = made + baseline * strength, attempts - made + (1 - baseline) * strength
    lo, hi = beta_distribution.ppf([0.025, 0.975], a, b)
    return {
        "made": made,
        "attempts": attempts,
        "observed": ratio(made, attempts),
        "baseline": baseline,
        "prior_attempts": strength,
        "posterior_mean": a / (a + b),
        "posterior_low95": float(lo),
        "posterior_high95": float(hi),
        "observed_weight": attempts / (attempts + strength),
    }


def shooting(
    team_rows: list[dict],
    previous: list[dict],
    league_prior: list[dict],
    opponent_prior: dict[str, list[dict]],
    continuity: float = 0.5,
) -> dict:
    """Own prior plus actual opponents' prior ability; no opponent FT 'skill'."""
    components = []
    league = totals(league_prior)
    prior = totals(previous)
    for defense in (False, True):
        current = totals(team_rows, defense)
        for kind, points, strength in SHOTS:
            lm, la = shot_counts(league, kind)
            if not la:
                continue
            league_rate = (lm + 0.5) / (la + 1)
            if defense:
                weighted = []
                for row in team_rows:
                    opponent = totals(opponent_prior.get(row["opponent"], []))
                    om, oa = shot_counts(opponent, kind)
                    weight = shot_counts(totals([row], True), kind)[1]
                    # Shrink each opponent's reference too; no naked 1/1 priors.
                    weighted.append((weight, (om + strength * league_rate) / (oa + strength)))
                den = sum(w for w, _ in weighted)
                baseline = sum(w * p for w, p in weighted) / den if den else league_rate
            else:
                pm, pa = shot_counts(prior, kind)
                own_reference = (pm + strength * league_rate) / (pa + strength)
                baseline = continuity * own_reference + (1 - continuity) * league_rate
            made, attempts = shot_counts(current, kind)
            if not attempts:
                continue
            post = posterior(made, attempts, baseline, strength)
            sign = -1 if defense else 1
            change = sign * points * (post["posterior_mean"] * attempts - made)
            post.update(
                {
                    "side": "allowed" if defense else "offense",
                    "shot_kind": kind,
                    "points_per100_shift": 100 * change / current["possessions"],
                    "baseline_policy": "opponents' prior offense" if defense else "roster-weighted team/league prior",
                    "sample_qualified": attempts >= 50,
                    "tone": tone(change, attempts >= 50),
                }
            )
            components.append(post)
    negative = sum(min(0, x["points_per100_shift"]) for x in components)
    positive = sum(max(0, x["points_per100_shift"]) for x in components)
    return {
        "components": components,
        "regression_pressure_pp100": -negative,
        "progression_pressure_pp100": positive,
        "net_reversion_pp100": positive + negative,
        "offense_shift_pp100": sum(x["points_per100_shift"] for x in components if x["side"] == "offense"),
        "defense_prevention_shift_pp100": sum(x["points_per100_shift"] for x in components if x["side"] == "allowed"),
        "forecast_weight": 0.0,
        "regression_tone": "unfavorable",
        "progression_tone": "favorable",
        "interpretation": "Conditional shooting reversion, not a probability or an overall team rating.",
    }


def pace(team_rows: list[dict], prior_rows: list[dict], league_prior: list[dict], regulation: float) -> dict:
    def values(rows):
        return [
            r["possessions"] * regulation / r["duration_minutes"]
            for r in rows
            if r.get("duration_minutes") and r["duration_minutes"] >= regulation
        ]

    now, past, league = values(team_rows), values(prior_rows), values(league_prior)
    reference = mean(past) if past else mean(league) if league else None
    if reference is None and not now:
        return {"state": "unavailable", "games": 0}
    if reference is None:
        reference = mean(now)
    k = 8.0
    center = (sum(now) + k * reference) / (len(now) + k)
    spread = max(2.0, pstdev(past or league or now))
    return {
        "state": "available",
        "games": len(now),
        "regulation_minutes": regulation,
        "observed_per_regulation": mean(now) if now else None,
        "prior_per_regulation": reference,
        "posterior_per_regulation": center,
        "recent5_per_regulation": mean(now[-5:]) if now else None,
        "game_sd": spread,
        "mean_se": spread / math.sqrt(len(now) + k),
        "prior_games": k,
        "duration_known_games": len(now),
        "policy": "Possessions normalized to regulation; eight-game prior weight; OT uses recorded duration.",
    }


def process(team_rows: list[dict]) -> dict:
    # Disjoint windows: recent games never appear in their comparison sample.
    recent, earlier = team_rows[-5:], team_rows[:-5]
    if len(earlier) < 3 or len(recent) < 3:
        return {"state": "thin", "recent_games": len(recent), "earlier_games": len(earlier), "rows": []}
    rows = []
    for defense in (False, True):
        a, b = rates(recent, defense), rates(earlier, defense)
        for key in a:
            if a[key] is None or b[key] is None:
                continue
            direction = -1 if key == "turnover_rate" else 1
            if key == "three_attempt_share":
                direction = 0
            if defense:
                direction *= -1
            delta = a[key] - b[key]
            rows.append(
                {
                    "side": "allowed" if defense else "offense",
                    "key": key,
                    "recent": a[key],
                    "earlier": b[key],
                    "change": delta,
                    "favorable_change": delta * direction if direction else None,
                    "tone": tone(delta * direction) if direction else "neutral",
                    "interpretation": "style" if not direction else "observed process change",
                }
            )
    return {"state": "available", "recent_games": len(recent), "earlier_games": len(earlier), "rows": rows}


def recent_reversion(
    team_rows: list[dict], current: list[dict], prior: dict[str, list[dict]], league_previous: list[dict]
) -> dict:
    if len(team_rows) < 8:
        return {"state": "thin", "recent_games": min(5, len(team_rows)), "earlier_games": max(0, len(team_rows) - 5)}
    recent = team_rows[-5:]
    prefix = before(current, recent[0]["date"])
    reference = defaultdict(list)
    for r in prefix:
        reference[r["team"]].append(r)
    own = reference[team_rows[0]["team"]]
    if len(own) < 3:
        return {"state": "thin", "recent_games": len(recent), "earlier_games": len(own)}
    opponents = {team: totals(reference.get(team) or seq) for team, seq in prior.items()}
    opponents.update({team: totals(seq) for team, seq in reference.items()})
    result = shooting(recent, own, league_previous or prefix, opponents, continuity=1)
    result.update(
        {
            "state": "available",
            "recent_games": len(recent),
            "earlier_games": len(own),
            "reference_cutoff": recent[0]["date"],
            "reference_policy": "Disjoint current prefix; opponent references censored before the recent window.",
        }
    )
    return result


def risk_window(profile: dict) -> dict:
    recent = profile.get("recent_shooting_reversion") or {}
    return recent if recent.get("state") == "available" else profile.get("shooting_reversion") or {}


def build(
    rows: list[dict], season: int, as_of: str, regulation: float, team_ids: list[str] | None = None
) -> dict[str, dict]:
    valid = before(rows, as_of)
    current = [r for r in valid if r["season"] == season]
    previous = [r for r in valid if r["season"] == season - 1]
    league_reference = previous or current
    league_totals = totals(league_reference)
    groups, prior = defaultdict(list), defaultdict(list)
    for r in current:
        groups[r["team"]].append(r)
    for r in previous:
        prior[r["team"]].append(r)
    prior_totals = {team: totals(seq) for team, seq in prior.items()}
    out = {}
    for team in sorted(set(groups) | set(prior) | set(team_ids or [])):
        seq = groups[team]
        known = sum(r.get("known_exposure", 0) for r in seq)
        retained = sum(r.get("returning_exposure", 0) for r in seq)
        continuity = retained / known if known else 0.5
        out[team] = {
            "schema": VERSION,
            "team": team,
            "season": season,
            "as_of": as_of,
            "games": len(seq),
            "through": max((r["date"] for r in seq), default=None),
            "pace": pace(seq, prior[team], league_reference, regulation),
            "shooting_reversion": shooting(seq, prior_totals.get(team, []), league_totals, prior_totals, continuity)
            if seq
            else None,
            "recent_shooting_reversion": recent_reversion(seq, current, prior, previous),
            "roster_continuity": {
                "returning_share": retained / known if known else None,
                "known_exposure": known,
                "returning_exposure": retained,
                "prior_reference_weight": continuity,
                "policy": "Observed exposure of prior-club players; unknown uses 50/50 team/league.",
            },
            "process": process(seq),
            "offense": rates(seq),
            "defense": rates(seq, True),
            "sample_state": "thin" if len(seq) < 8 else "established",
            "formation_policy": "Personnel composition does not establish geometric sets or coverage.",
        }
    return out


def matchup(away: dict, home: dict) -> dict:
    if not away or not home:
        return {"state": "unavailable"}
    a, h = away["pace"], home["pace"]
    if a.get("state") != "available" or h.get("state") != "available":
        return {"state": "unavailable"}
    center = (a["posterior_per_regulation"] + h["posterior_per_regulation"]) / 2
    pace_sd = math.sqrt((a["game_sd"] ** 2 + h["game_sd"] ** 2) / 2)
    return {
        "state": "available",
        "pace_center": center,
        "pace_game_sd": pace_sd,
        "pace_conflict": abs(a["posterior_per_regulation"] - h["posterior_per_regulation"]),
        "away_reversion_pp100": risk_window(away).get("net_reversion_pp100"),
        "home_reversion_pp100": risk_window(home).get("net_reversion_pp100"),
        "away_risk_window": "recent5"
        if (away.get("recent_shooting_reversion") or {}).get("state") == "available"
        else "season",
        "home_risk_window": "recent5"
        if (home.get("recent_shooting_reversion") or {}).get("state") == "available"
        else "season",
        "sample_state": "thin" if "thin" in (away["sample_state"], home["sample_state"]) else "established",
    }


def personnel_matchup(offense: dict, defense: dict) -> list[dict]:
    """Read offense against the opponent's observed defensive personnel mix.

    These are descriptive cells, not a causal matchup adjustment. Excluded
    lineups never enter the usage denominator and thin cells remain visible.
    """
    offense_rows = {
        r["personnel"]: r
        for r in offense.get("personnel_formations", [])
        if r["side"] == "offense" and r["basis"] == "opponent"
    }
    cells = []
    for d in defense.get("personnel_formations", []):
        if d["side"] != "defense" or d["basis"] != "own":
            continue
        a = offense_rows.get(d["personnel"])
        cells.append(
            {
                "personnel": d["personnel"],
                "opponent_usage": d["usage"],
                "offense_ppp_against": a["ppp"] if a else None,
                "opponent_ppp_allowed": d["ppp"],
                "offense_possessions": a["possessions"] if a else 0,
                "opponent_possessions": d["possessions"],
                "sample_qualified": bool(a and a["sample_qualified"] and d["sample_qualified"]),
                "forecast_weight": 0.0,
            }
        )
    return sorted(cells, key=lambda r: -r["opponent_usage"])


def personnel(rows: list[dict], as_of: str) -> dict:
    """Possession-weighted observed roster composition; exact/estimated N labeled by adapter."""
    cutoff = utc(as_of)
    acc = defaultdict(lambda: defaultdict(lambda: {"possessions": 0.0, "points": 0.0, "games": set()}))
    totals_by_side = defaultdict(float)
    for r in rows:
        if utc(r.get("available_at", r["date"])) >= cutoff:
            continue
        for team, side, own, opponent in (
            (r["team"], "offense", r["own_personnel"], r["opponent_personnel"]),
            (r["opponent"], "defense", r["opponent_personnel"], r["own_personnel"]),
        ):
            totals_by_side[(team, side)] += r["possessions"]
            for basis, label in (("own", own), ("opponent", opponent)):
                b = acc[team][(side, basis, label)]
                b["possessions"] += r["possessions"]
                b["points"] += r["points"]
                b["games"].add(r["game_id"])
    return {
        team: [
            {
                "side": side,
                "basis": basis,
                "personnel": label,
                "possessions": b["possessions"],
                "points": b["points"],
                "games": len(b["games"]),
                "usage": b["possessions"] / totals_by_side[(team, side)],
                "ppp": ratio(b["points"], b["possessions"]),
                "sample_qualified": b["possessions"] >= 100 and len(b["games"]) >= 5,
            }
            for (side, basis, label), b in sorted(values.items())
        ]
        for team, values in acc.items()
    }
