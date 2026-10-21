"""The three published artifacts.

* ``board.json`` - the model board (projections, market, authority). Read by
  chase-analytics.com's Model Center only.
* ``slate.json`` - the public research slate: identity, venue, rest, injuries,
  observed team rates and player lines. No model output of any kind; the
  public matchup desk reads this, and ``PUBLIC_FORBIDDEN`` is enforced on it.
* ``build.json`` - when the board was built, from what, and what went wrong.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import authority, availability, features, ingest, live, player_board, signals, teams
from .backtest import season_end
from .ratings import ABBRS, CONSTANTS, PARAMS, Ratings, fit, local_day, prior_from, project, rest_flags

BOARD_SCHEMA = "nba-model/board/1"
PUBLIC_SLATE_SCHEMA = "chase-public-slate/1"
MODEL_VERSION = "nba-rapm-2.0"

PUBLIC_FORBIDDEN = {
    "model_margin", "market_margin", "market_gap", "published_margin", "edge_points",
    "edge_withheld_reason", "win_probability", "projected_away_score", "projected_home_score",
    "projected_total", "may_bet", "authority", "book", "action", "rating", "net_rating_model",
    "lean", "pick", "availability_margin",
}


def current_season(now: datetime) -> int:
    """ESPN's season year: a season starting in October ends next calendar year."""
    d = now.astimezone(live.ET)
    return d.year + 1 if d.month >= 8 else d.year


def roster_shift(season: int, roster: dict[str, dict[str, str]]) -> dict[str, float]:
    """Prior net shift (points/100) for each club's roster turnover since last season."""
    prev = availability.lines(ingest.load_players(season - 1))
    vals = availability.season_values(prev, availability.lines(ingest.load_players(season - 2)))
    last = availability.last_team_value(prev, vals)
    return {t: authority.ROSTER_SCALE * (availability.roster_value(people, vals) - last.get(t, 0.0))
            for t, people in roster.items() if people}


def ratings_chain(season: int, as_of: datetime,
                  shift: dict[str, float] | None = None) -> tuple[Ratings, Ratings]:
    """(prior for ``season``, ratings as of ``as_of``) from the cached seasons."""
    prev: Ratings | None = None
    for s in range(season - 3, season):
        games = ingest.load_games(s)
        if not games:
            continue
        prev = season_end(games, prior_from(prev, PARAMS), PARAMS, CONSTANTS)
    prior = prior_from(prev, PARAMS, shift)
    games = ingest.load_games(season)
    now_r = fit(games, as_of=as_of, prior=prior, params=PARAMS, const=CONSTANTS, rest=rest_flags(games))
    return prior, now_r


def _played_yesterday(games: list[ingest.Game], team: str, tip_utc: str) -> bool:
    day = local_day(tip_utc) - timedelta(days=1)
    return any(local_day(g.date_utc) == day and team in (g.home, g.away) for g in games)


def _rest_days(games: list[ingest.Game], team: str, tip_utc: str) -> int | None:
    tip = local_day(tip_utc)
    prior = [local_day(g.date_utc) for g in games if team in (g.home, g.away) and local_day(g.date_utc) < tip]
    return (tip - max(prior)).days - 1 if prior else None


def _round(v: float | None, n: int = 1) -> float | None:
    return None if v is None else round(v, n)


def build(now: datetime | None = None, *, out: Path) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    season = current_season(now)
    issues: list[str] = []
    slate_date, fixtures = live.slate(now)
    try:
        injury_rows = live.injuries()
    except Exception as exc:  # the board still ships; availability is then unknown
        injury_rows = []
        issues.append(f"injury report unavailable: {type(exc).__name__}")
    try:
        roster = live.rosters(teams.all_abbrs())
    except Exception as exc:  # no roster shift and no availability term tonight
        roster = {}
        issues.append(f"rosters unavailable: {type(exc).__name__}")

    games = ingest.load_games(season)
    prev_games = ingest.load_games(season - 1)
    first_tip = min((f.tip_utc for f in fixtures), default=now.isoformat())
    as_of = min(now, datetime.fromisoformat(first_tip.replace("Z", "+00:00")))
    shift = roster_shift(season, roster) if roster else {}
    prior, rating = ratings_chain(season, as_of, shift)
    prev_lines = availability.lines(ingest.load_players(season - 1))
    tracker = availability.Tracker(games, availability.lines(ingest.load_players(season)),
                                   prev_lines).prepare()
    prev_vals = availability.season_values(prev_lines, availability.lines(ingest.load_players(season - 2)))
    out_by_team: dict[str, set[str]] = {}
    for inj in injury_rows:
        if inj.unavailable and inj.player_id:
            out_by_team.setdefault(inj.team, set()).add(inj.player_id)

    try:
        pm = player_board.build(season, as_of)
    except Exception as exc:  # the team model still prices the slate
        pm = None
        issues.append(f"player model unavailable: {type(exc).__name__}: {exc}")
    sig_record = signals.record()

    form_now = features.team_form(games, season)
    form_prev = features.team_form(prev_games, season - 1)
    team_of = {pid: t for t, people in roster.items() for pid in people}
    lines_now = features.player_lines(ingest.load_players(season), team_of if roster else None)
    lines_prev = features.player_lines(ingest.load_players(season - 1), team_of if roster else None)

    board_games: list[dict[str, Any]] = []
    public_games: list[dict[str, Any]] = []
    for f in fixtures:
        hb = int(_played_yesterday(games, f.home, f.tip_utc))
        ab = int(_played_yesterday(games, f.away, f.tip_utc))
        p = project(rating, f.home, f.away, const=CONSTANTS, neutral=f.neutral, home_b2b=hb, away_b2b=ab)
        avail: dict[str, Any] = {}
        adj = {}
        for side, team in (("home", f.home), ("away", f.away)):
            people = roster.get(team, {})
            pool = set(people) - out_by_team.get(team, set())
            if not people:
                adj[side], avail[side] = 0.0, []
            elif rating.games.get(team, 0) >= EARLY_GAMES:
                adj[side], avail[side] = tracker.adjustment(team, f"next:{f.event_id}", pool)
            else:
                adj[side], avail[side] = early_availability(people, pool, prev_vals)
        avail_margin = authority.AVAIL_SCALE * p.possessions / 100.0 * (adj["home"] - adj["away"])
        team_margin = p.margin + avail_margin
        # The player model (RAPM x tonight's rotation) is the primary forecast;
        # the team model stands in when rosters or the fit are unavailable.
        pj = None
        if pm is not None and roster.get(f.home) and roster.get(f.away):
            pj = player_board.project(pm, f.home, f.away,
                                      set(roster[f.home]) - out_by_team.get(f.home, set()),
                                      set(roster[f.away]) - out_by_team.get(f.away, set()),
                                      p.possessions, f.neutral)
        model_margin = pj["margin"] if pj else team_margin
        model_total = pj["total"] if pj else p.total
        home_pts = pj["home_pts"] if pj else p.home_pts + avail_margin / 2
        away_pts = pj["away_pts"] if pj else p.away_pts - avail_margin / 2
        win_p = availability_win_prob(model_margin)
        book = f.book or {}
        market_margin = book.get("margin")
        market_total = book.get("total")
        published = market_margin if market_margin is not None else model_margin
        board_games.append({
            "id": f.event_id, "key": f"{f.away}@{f.home}", "away": f.away, "home": f.home,
            "away_name": teams.name(f.away), "home_name": teams.name(f.home),
            "away_logo": teams.logo(f.away), "home_logo": teams.logo(f.home),
            "away_color": teams.color(f.away), "home_color": teams.color(f.home),
            "kickoff_utc": f.tip_utc, "game_state": f.state, "neutral": f.neutral,
            "away_record": f.away_record, "home_record": f.home_record,
            "away_score": f.away_score, "home_score": f.home_score,
            "forecast_source": "player_rapm" if pj else "team_ratings",
            "team_margin": _round(team_margin, 2), "team_total": _round(p.total, 1),
            "rating_margin": _round(p.margin, 2), "availability_margin": _round(avail_margin, 2),
            "model_margin": _round(model_margin, 2),
            "market_margin": market_margin, "published_margin": _round(published, 2),
            "market_gap": None if market_margin is None else _round(model_margin - market_margin, 2),
            "edge_points": None,
            "edge_withheld_reason": authority.EDGE_WITHHELD,
            "win_probability": round(win_p, 4),
            "projected_home_score": _round(home_pts, 1),
            "projected_away_score": _round(away_pts, 1),
            "projected_total": _round(model_total, 1), "projected_possessions": _round(p.possessions, 1),
            "market_total": market_total,
            "total_gap": None if market_total is None else _round(model_total - market_total, 1),
            "signals": signals.for_game(model_margin, market_margin, model_total, market_total,
                                        f.home, f.away, sig_record) if f.state == "scheduled" else [],
            "rotation": {"home": pj["home_rotation"], "away": pj["away_rotation"]} if pj else None,
            "home_b2b": bool(hb), "away_b2b": bool(ab),
            "availability": {"home": avail["home"][:6], "away": avail["away"][:6]},
            "book": book or None,
            "home_moneyline": book.get("home_moneyline"), "away_moneyline": book.get("away_moneyline"),
            "action": "MONITOR", "authority": authority.LEVEL,
        })
        public = {
            "id": f.event_id, "sport": "nba", "season": season, "game_state": f.state,
            "status_detail": f.status_detail, "kickoff_utc": f.tip_utc,
            "away": f.away, "home": f.home, "away_name": teams.name(f.away), "home_name": teams.name(f.home),
            "away_logo": teams.logo(f.away), "home_logo": teams.logo(f.home),
            "away_color": teams.color(f.away), "home_color": teams.color(f.home),
            "away_record": f.away_record, "home_record": f.home_record,
            "away_split_record": f.away_road_record, "home_split_record": f.home_home_record,
            "away_score": f.away_score, "home_score": f.home_score,
            "venue": f.venue, "venue_city": f.venue_city, "broadcast": f.broadcast, "neutral": f.neutral,
        }
        for side, team, b2b in (("away", f.away, ab), ("home", f.home, hb)):
            cur = form_now.get(team)
            public[f"{side}_form"] = cur
            public[f"{side}_form_prior"] = form_prev.get(team)
            public[f"{side}_rest_days"] = _rest_days(games, team, f.tip_utc)
            public[f"{side}_b2b"] = bool(b2b)
            public[f"{side}_game_log"] = features.game_log(games, team)
            public[f"{side}_players"] = (lines_now.get(team) or [])[:12]
            public[f"{side}_players_prior"] = (lines_prev.get(team) or [])[:12]
            public[f"{side}_injuries"] = [
                {"player_id": i.player_id, "player_name": i.name, "status": i.status,
                 "detail": i.detail, "comment": i.note, "date": i.date, "return_date": i.return_date}
                for i in injury_rows if i.team == team]
        public_games.append(public)

    generated = now.replace(microsecond=0).isoformat()
    record = authority.record()
    board = {
        "schema": BOARD_SCHEMA, "model_version": MODEL_VERSION, "generated_at_utc": generated,
        "season": season, "slate_date": slate_date,
        "authority": authority.LEVEL, "may_bet": False, "unmet_gates": authority.UNMET_GATES,
        "evidence": authority.EVIDENCE, "note": authority.NOTE,
        "constants": {"home_eff_per100": round(CONSTANTS.home_eff, 3), "b2b_off_per100": round(CONSTANTS.b2b_off, 3),
                      "b2b_def_per100": round(CONSTANTS.b2b_def, 3), "margin_sigma": round(CONSTANTS.margin_sigma, 2),
                      "total_sigma": round(CONSTANTS.total_sigma, 2), "availability_scale": authority.AVAIL_SCALE,
                      **{k: v for k, v in PARAMS.__dict__.items()}},
        "teams": [{"team": a, "name": teams.name(a), "off": round(rating.off[a], 2), "def": round(rating.deff[a], 2),
                   "net": round(rating.net(a), 2), "pace": round(rating.mu_pace + rating.pace[a], 1),
                   "prior_net": round(prior.off[a] - prior.deff[a], 2),
                   "roster_shift": round(shift.get(a, 0.0), 2), "games": rating.games.get(a, 0)}
                  for a in sorted(ABBRS, key=lambda a: -rating.net(a))],
        "games": board_games, "backtest": record, "signals_record": sig_record,
        "signal_note": ("A signal is a gap between the player model and the CURRENT DraftKings line. Its record "
                        "was measured against the consensus OPENING line, 2023-24 to 2025-26; the model does not "
                        "beat the closing line, so a signal read after the line has moved is worth less."),
        "sources": {"games": "ESPN game summaries (box score + DraftKings close)",
                    "slate": "ESPN scoreboard", "injuries": "ESPN injury report", "rosters": "ESPN team rosters"},
    }
    public_slate = {"schema": PUBLIC_SLATE_SCHEMA, "sport": "nba", "season": season, "slate_date": slate_date,
                    "generated_at_utc": generated, "data_through_utc": max((g.date_utc for g in games), default=None),
                    "games": public_games}
    leaked = forbidden_keys(public_slate)
    if leaked:
        raise ValueError(f"public slate carries model fields: {sorted(leaked)}")
    build_info = {"generated_at_utc": generated, "state": "ok" if not issues else "degraded", "issues": issues,
                  "season": season, "slate_date": slate_date, "games": len(fixtures),
                  "cached_games": len(games), "injuries": len(injury_rows), "model_version": MODEL_VERSION}
    out.mkdir(parents=True, exist_ok=True)
    for name, payload in (("board.json", board), ("slate.json", public_slate), ("build.json", build_info)):
        (out / name).write_text(json.dumps(payload, indent=1, default=str) + "\n", encoding="utf-8")
    return board


EARLY_GAMES = 3


def early_availability(people: dict[str, str], pool: set[str],
                       vals: dict[str, tuple[float, float]]) -> tuple[float, list[dict[str, Any]]]:
    """Before a club has a recent window: tonight's available roster against the full
    roster, both at last season's usage (the same units as the tracker)."""
    signal = availability.roster_value(pool, vals) - availability.roster_value(people, vals)
    movers = [{"player_id": p, "name": people[p], "out": True,
               "minutes_share": round(vals[p][1] * 5, 3), "value": round(vals[p][0], 3)}
              for p in people if p not in pool and p in vals]
    movers.sort(key=lambda m: -m["minutes_share"] * m["value"])
    return signal, movers


def availability_win_prob(margin: float) -> float:
    from .ratings import norm_cdf
    return norm_cdf(margin / CONSTANTS.margin_sigma)


def forbidden_keys(obj: Any, found: set[str] | None = None) -> set[str]:
    found = set() if found is None else found
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in PUBLIC_FORBIDDEN:
                found.add(k)
            forbidden_keys(v, found)
    elif isinstance(obj, list):
        for v in obj:
            forbidden_keys(v, found)
    return found
