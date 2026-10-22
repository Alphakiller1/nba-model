"""Club and player matchup profiles: how a team plays, who it plays, and what tends to regress.

Every figure here is published or observed - nothing is projected:

* shot profile       stats.nba.com shot locations by zone (own and allowed), ESPN play-by-play
                     shot kinds (drives, pull-ups, catch-and-shoot, cuts, putbacks) and transition
* play types         Synergy (stats.nba.com): frequency, points per possession, percentile,
                     offense and defense, eleven play types
* tracking           Second Spectrum (stats.nba.com): speed, distance, drives, catch-and-shoot,
                     pull-ups, post touches
* size and age       rotation heights, weights and ages, minutes-weighted (stats.nba.com bio)
* coach and style    head coach and staff; a style read derived only from the ranks above
* regression watch   the gaps that history says close: opponent three-point luck, close-game
                     record against point differential, Pythagorean wins, shooting against a
                     player's own track record, and the league's historical change at each age
* player profiles    each rotation player's zones, top play types, tracking, defense by zone
                     (opponents' FG% against him vs their normal FG%), and age

The basis is the current season once a club has played ``MIN_GAMES``; before
that, last season's full record, and every block says which it is.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

from . import aging, bbref, bulk, ingest, nbastats, teams

MIN_GAMES = 5
ZONES = (("rim", "Restricted Area", "At The Rim"), ("paint", "In The Paint (Non-RA)", "Paint (Non-RA)"),
         ("mid", "Mid-Range", "Mid-Range"), ("corner3", "Corner 3", "Corner Three"),
         ("above3", "Above the Break 3", "Above-The-Break Three"))
# ESPN credits assists only on made shots, so an "assisted jumper" kind would hold
# only makes; catch-and-shoot comes from Second Spectrum tracking instead, and
# every other jump shot is one "jumper" kind.
KINDS = (("drive", "Drives"), ("rim_finish", "Rim Finishes"), ("cut", "Cuts"), ("putback", "Putbacks"),
         ("post", "Post Moves"), ("floater", "Floaters"), ("pullup", "Pull-Ups"), ("stepback", "Step-Backs"),
         ("jumper", "Other Jumpers"))
DEF_ZONES = (("Less Than 6Ft", "LT_06_PCT", "NS_LT_06_PCT", "FGA_LT_06", "Within 6 Ft"),
             ("Less Than 10Ft", "LT_10_PCT", "NS_LT_10_PCT", "FGA_LT_10", "Within 10 Ft"),
             ("Greater Than 15Ft", "GT_15_PCT", "NS_GT_15_PCT", "FGA_GT_15", "Beyond 15 Ft"),
             ("3 Pointers", "FG3_PCT", "NS_FG3_PCT", "FG3A", "Three-Pointers"),
             ("Overall", "D_FG_PCT", "NORMAL_FG_PCT", "D_FGA", "All Shots"))


def _f(v) -> float | None:
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def _rank(pool: list[float], value: float, better: str) -> int:
    if better == "low":
        return 1 + sum(1 for v in pool if v < value - 1e-12)
    return 1 + sum(1 for v in pool if v > value + 1e-12)


def ranked(values: dict[str, float | None], label: str, better: str, fmt: str) -> dict[str, dict]:
    """team -> {label, value, better, rank, of, format}; 'neutral' ranks most-first."""
    pool = [v for v in values.values() if v is not None]
    by = "high" if better == "neutral" else better
    return {t: {"label": label, "value": round(v, 4), "better": better, "rank": _rank(pool, v, by),
                "of": len(pool), "format": fmt}
            for t, v in values.items() if v is not None}


def season_label(season: int) -> str:
    return f"{season - 1}-{str(season)[2:]}"


class Tables:
    """stats.nba.com snapshots for one season, keyed to ESPN team codes."""

    def __init__(self, season: int):
        self.season = season
        self.cache: dict[str, list[dict]] = {}
        self.fetched: dict[str, str | None] = {}
        self.team_of_id: dict[str, str] = {}
        for name in ("synergy_T_offensive_Transition", "tracking_Team_SpeedDistance", "tracking_Team_Drives"):
            for r in self.rows(name):
                if r.get("TEAM_ID") and r.get("TEAM_ABBREVIATION"):
                    self.team_of_id[str(r["TEAM_ID"])] = teams.canon(r["TEAM_ABBREVIATION"])

    def rows(self, name: str) -> list[dict]:
        if name not in self.cache:
            self.cache[name], self.fetched[name] = nbastats.load(self.season, name)
        return self.cache[name]

    def team(self, r: dict) -> str | None:
        code = r.get("TEAM_ABBREVIATION") or r.get("PLAYER_LAST_TEAM_ABBREVIATION")
        if code:
            return teams.canon(code)
        return self.team_of_id.get(str(r.get("TEAM_ID") or ""))

    def available(self) -> bool:
        return bool(self.rows("synergy_T_offensive_Transition"))

    def games_played(self) -> float:
        gp = [_f(r.get("GP")) for r in self.rows("synergy_T_offensive_Transition")]
        gp = [g for g in gp if g is not None]
        return sorted(gp)[len(gp) // 2] if gp else 0.0

    def as_of(self) -> str | None:
        vals = [v for v in self.fetched.values() if v]
        return max(vals) if vals else None


def basis_tables(season: int) -> Tables:
    now = Tables(season)
    if now.available() and now.games_played() >= MIN_GAMES:
        return now
    prev = Tables(season - 1)
    return prev if prev.available() else now


# -- team blocks ----------------------------------------------------------------

def shot_profile(t: Tables) -> dict[str, dict]:
    out: dict[str, dict] = defaultdict(dict)
    for measure, side, prefix in (("shots_Team_Base", "offense", ""), ("shots_Team_Opponent", "defense", "OPP_")):
        rows = t.rows(measure)
        per: dict[str, dict[str, tuple[float, float]]] = {}
        for r in rows:
            team = t.team_of_id.get(str(r.get("TEAM_ID")))
            if not team:
                continue
            z = {}
            for key, src, _ in ZONES:
                fga = _f(r.get(f"{src}|{prefix}FGA"))
                pct = _f(r.get(f"{src}|{prefix}FG_PCT"))
                z[key] = (fga or 0.0, pct)
            per[team] = z
        for key, _, label in ZONES:
            freq = {tm: (z[key][0] / sum(v[0] for v in z.values())) if sum(v[0] for v in z.values()) else None
                    for tm, z in per.items()}
            pct = {tm: z[key][1] for tm, z in per.items()}
            who = "" if side == "offense" else " Allowed"
            f_rank = ranked(freq, f"{label} Share Of Shots{who}", "neutral" if side == "offense" else "neutral", "pct")
            p_rank = ranked(pct, f"{label} FG%{who}", "high" if side == "offense" else "low", "pct")
            for tm in per:
                out[tm].setdefault(side, {})[key] = {"label": label, "share": f_rank.get(tm), "fg": p_rank.get(tm)}
    return dict(out)


def espn_shot_kinds(season: int) -> dict[str, dict]:
    """Per club: share and FG% of each shot kind, transition share, assisted share (ESPN play-by-play)."""
    games = {g.event_id: g for g in ingest.load_games(season)}
    tally: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(lambda: [0, 0, 0]))
    for r in bulk.load_shots(season):
        g = games.get(r["event_id"])
        if not g:
            continue
        team = g.home if r["side"] == "home" else g.away
        made, value = int(r["made"]), int(r["value"])
        kind = "jumper" if r["kind"] == "catch_shoot" else r["kind"]
        for key in (kind, "_all") + (("_transition",) if r["transition"] == "1" else ()) + (
                ("_assisted",) if r["assister"] else ()):
            t = tally[team][key]
            t[0] += 1
            t[1] += made
            t[2] += made * value
    out: dict[str, dict] = {}
    share: dict[str, dict[str, float | None]] = defaultdict(dict)
    eff: dict[str, dict[str, float | None]] = defaultdict(dict)
    for team, k in tally.items():
        n = k["_all"][0] or 1
        for key, _ in KINDS + (("_transition", ""), ("_assisted", "")):
            # ESPN credits assists only on makes, so the assisted share is of made shots.
            share[key][team] = (k[key][0] / k["_all"][1]) if key == "_assisted" and k["_all"][1] else k[key][0] / n
            eff[key][team] = (k[key][2] / (2 * k[key][0])) if k[key][0] >= 15 and key != "_assisted" else None
    for key, label in KINDS + (("_transition", "Transition"), ("_assisted", "Assisted")):
        s_r = ranked(share[key], "Share Of Made Shots Assisted" if key == "_assisted" else f"{label} Share Of Shots",
                     "neutral", "pct")
        e_r = ranked(eff[key], f"{label} Effective FG%", "high", "pct")
        for team in tally:
            out.setdefault(team, {})[key.strip("_")] = {"label": label, "share": s_r.get(team), "efg": e_r.get(team)}
    return out


def play_types(t: Tables) -> dict[str, dict]:
    out: dict[str, dict] = defaultdict(lambda: {"offense": {}, "defense": {}})
    for grouping, side in (("offensive", "offense"), ("defensive", "defense")):
        for pt in nbastats.PLAY_TYPES:
            rows = t.rows(f"synergy_T_{grouping}_{pt}")
            freq = {t.team(r): _f(r.get("POSS_PCT")) for r in rows if t.team(r)}
            ppp = {t.team(r): _f(r.get("PPP")) for r in rows if t.team(r)}
            label = nbastats.PLAY_TYPE_LABEL[pt]
            fr = ranked(freq, f"{label} Frequency" + ("" if side == "offense" else " Faced"), "neutral", "pct")
            pr = ranked(ppp, f"{label} Points Per Possession" + ("" if side == "offense" else " Allowed"),
                        "high" if side == "offense" else "low", "num2")
            for team in freq:
                out[team][side][pt] = {"label": label, "freq": fr.get(team), "ppp": pr.get(team)}
    return dict(out)


def tracking(t: Tables) -> dict[str, dict]:
    spec = (("tracking_Team_SpeedDistance", "AVG_SPEED", "Average Speed (mph)", "neutral", "num2"),
            ("tracking_Team_SpeedDistance", "AVG_SPEED_OFF", "Offensive Speed (mph)", "neutral", "num2"),
            ("tracking_Team_SpeedDistance", "AVG_SPEED_DEF", "Defensive Speed (mph)", "neutral", "num2"),
            ("tracking_Team_SpeedDistance", "DIST_MILES", "Miles Run Per Game", "neutral", "num"),
            ("tracking_Team_Drives", "DRIVES", "Drives Per Game", "neutral", "num"),
            ("tracking_Team_Drives", "DRIVE_FG_PCT", "FG% On Drives", "high", "pct"),
            ("tracking_Team_Drives", "DRIVE_PTS", "Points From Drives", "high", "num"),
            ("tracking_Team_CatchShoot", "CATCH_SHOOT_FGA", "Catch-And-Shoot Attempts", "neutral", "num"),
            ("tracking_Team_CatchShoot", "CATCH_SHOOT_EFG_PCT", "Catch-And-Shoot eFG%", "high", "pct"),
            ("tracking_Team_PullUpShot", "PULL_UP_FGA", "Pull-Up Attempts", "neutral", "num"),
            ("tracking_Team_PullUpShot", "PULL_UP_EFG_PCT", "Pull-Up eFG%", "high", "pct"),
            ("tracking_Team_PostTouch", "POST_TOUCHES", "Post Touches", "neutral", "num"),
            ("tracking_Team_Rebounding", "OREB_CHANCE_PCT", "Offensive Rebound Chance Conversion", "high", "pct"))
    out: dict[str, dict] = defaultdict(dict)
    for table, col, label, better, fmt in spec:
        vals = {t.team(r): _f(r.get(col)) for r in t.rows(table) if t.team(r)}
        for team, entry in ranked(vals, label, better, fmt).items():
            out[team][col.lower()] = entry
    return dict(out)


def _bio(t: Tables) -> dict[str, dict]:
    return {bbref.norm(r.get("PLAYER_NAME", "")): r for r in t.rows("bio")}


def size(t: Tables, rotations: dict[str, list[dict]]) -> dict[str, dict]:
    """Minutes-weighted height, weight and age of each club's rotation."""
    bio = _bio(t)
    raw: dict[str, dict[str, float | None]] = defaultdict(dict)
    for team, players in rotations.items():
        acc = {"h": [0.0, 0.0], "w": [0.0, 0.0], "a": [0.0, 0.0]}
        tallest = None
        for p in players:
            b = bio.get(bbref.norm(p.get("player_name", "")))
            m = float(p.get("mpg") or 0)
            if not b or m <= 0:
                continue
            for key, col in (("h", "PLAYER_HEIGHT_INCHES"), ("w", "PLAYER_WEIGHT"), ("a", "AGE")):
                v = _f(b.get(col))
                if v is not None:
                    acc[key][0] += m * v
                    acc[key][1] += m
            h = _f(b.get("PLAYER_HEIGHT_INCHES"))
            if h is not None and (tallest is None or h > tallest):
                tallest = h
        for key, name in (("h", "height"), ("w", "weight"), ("a", "age")):
            raw[name][team] = acc[key][0] / acc[key][1] if acc[key][1] else None
        raw["tallest"][team] = tallest
    out: dict[str, dict] = defaultdict(dict)
    for name, label, fmt in (("height", "Rotation Height (in, minutes-weighted)", "num"),
                             ("weight", "Rotation Weight (lb, minutes-weighted)", "num"),
                             ("age", "Rotation Age (minutes-weighted)", "num"),
                             ("tallest", "Tallest Regular (in)", "num")):
        for team, entry in ranked(raw[name], label, "neutral", fmt).items():
            out[team][name] = entry
    return dict(out)


def coaches(t_now: Tables, t_prev: Tables, espn_now: dict | None = None,
            espn_prev: dict | None = None) -> dict[str, dict]:
    """Head coach from ESPN (current, complete); staff size from the league's staff feed."""
    def heads(t: Tables) -> dict[str, dict]:
        out = {}
        for r in t.rows("coaches"):
            team = t.team_of_id.get(str(r.get("TEAM_ID")))
            if team and r.get("COACH_TYPE") == "Head Coach":
                out[team] = r
        return out
    ids = {**t_prev.team_of_id, **t_now.team_of_id}
    t_now.team_of_id = ids
    t_prev.team_of_id = ids
    now = {**heads(t_prev), **heads(t_now)}
    # Staff counts per club from this season's feed where it has the club, else last season's.
    staff_now: dict[str, int] = defaultdict(int)
    staff_prev: dict[str, int] = defaultdict(int)
    for rows, bucket in ((t_now.rows("coaches"), staff_now), (t_prev.rows("coaches"), staff_prev)):
        for r in rows:
            team = ids.get(str(r.get("TEAM_ID")))
            if team:
                bucket[team] += 1
    staff = {**staff_prev, **staff_now}
    espn_now, espn_prev = espn_now or {}, espn_prev or {}
    out = {}
    for team in teams.all_abbrs():
        cur = (espn_now.get(team) or {}).get("name") or (now.get(team) or {}).get("COACH_NAME")
        if not cur:
            continue
        # Neither ESPN (it answers a past season with today's coach) nor the
        # league's staff feed (it lists today's staff for any season) gives last
        # season's coach reliably, so the profile states his years as a head
        # coach and nothing about tenure with this club.
        exp = (espn_now.get(team) or {}).get("experience")
        out[team] = {"head_coach": cur, "staff_size": staff.get(team), "head_coach_seasons": exp}
    return out


# Style tags: each is a plain statement of where a club ranks; a tag is shown
# only when the rank is extreme (top or bottom five of thirty).
STYLE = (
    ("pace", "Plays Fast", "Plays Slow"),
    ("three_share", "Lives Behind The Arc", "Avoids The Three"),
    ("rim_share", "Attacks The Rim", "Rarely Reaches The Rim"),
    ("mid_share", "Mid-Range Heavy", "Avoids The Mid-Range"),
    ("pnr", "Pick-And-Roll Heavy", "Light On Pick-And-Roll"),
    ("iso", "Isolation Heavy", "Rarely Isolates"),
    ("post", "Post-Up Hub", "Rarely Posts Up"),
    ("transition", "Runs In Transition", "Rarely Runs"),
    ("oreb", "Crashes The Offensive Glass", "Gets Back Instead Of Crashing"),
    ("assisted", "Ball Movement Offense", "Self-Creation Offense"),
    ("rim_allowed", "Concedes The Rim", "Walls Off The Rim"),
    ("three_allowed", "Concedes Threes", "Runs Shooters Off The Line"),
)


def style(team: str, shots: dict, pts: dict, kinds: dict, form: dict) -> list[dict]:
    off = (shots.get(team) or {}).get("offense") or {}
    dfn = (shots.get(team) or {}).get("defense") or {}
    pt = (pts.get(team) or {}).get("offense") or {}
    rates = (form.get(team) or {}).get("rates") or {}

    def rank_of(entry):
        e = entry or {}
        return e.get("rank"), e.get("of"), e.get("value"), e.get("label")

    pnr_rank = None
    if pt.get("PRBallHandler") and pt.get("PRRollman"):
        pnr_rank = min(pt["PRBallHandler"]["freq"]["rank"], pt["PRRollman"]["freq"]["rank"])
    sources = {
        "pace": rank_of(rates.get("pace")),
        "three_share": rank_of(rates.get("off_three_rate")),
        "rim_share": rank_of((off.get("rim") or {}).get("share")),
        "mid_share": rank_of((off.get("mid") or {}).get("share")),
        "pnr": (pnr_rank, 30, round(pt["PRBallHandler"]["freq"]["value"] + pt["PRRollman"]["freq"]["value"], 4),
                "Pick-And-Roll Share Of Possessions (Ball Handler + Roll Man)")
        if pnr_rank else (None, None, None, None),
        "iso": rank_of(((pt.get("Isolation") or {}).get("freq"))),
        "post": rank_of(((pt.get("Postup") or {}).get("freq"))),
        "transition": rank_of(((pt.get("Transition") or {}).get("freq"))),
        "oreb": rank_of(rates.get("off_orb")),
        "assisted": rank_of(((kinds.get(team) or {}).get("assisted") or {}).get("share")),
        "rim_allowed": rank_of((dfn.get("rim") or {}).get("share")),
        "three_allowed": rank_of(rates.get("def_three_rate")),
    }
    tags = []
    for key, high, low in STYLE:
        r, of, value, label = sources.get(key, (None, None, None, None))
        if not r or not of or value is None:
            continue
        if r <= 5 or r > of - 5:
            tags.append({"key": key, "tag": high if r <= 5 else low, "label": label, "value": value,
                         "rank": r, "of": of})
    return tags


def regression(games: list[ingest.Game]) -> dict[str, dict]:
    """The gaps history says close, per club, from the season's box scores."""
    tot: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    lg = defaultdict(float)
    for g in games:
        for me, op in (("home", "away"), ("away", "home")):
            team = getattr(g, me)
            t = tot[team]
            us, them = getattr(g, f"{me}_pts"), getattr(g, f"{op}_pts")
            t["g"] += 1
            t["w"] += us > them
            t["pf"] += us
            t["pa"] += them
            t["o3m"] += getattr(g, f"{op}_3pm")
            t["o3a"] += getattr(g, f"{op}_3pa")
            t["3m"] += getattr(g, f"{me}_3pm")
            t["3a"] += getattr(g, f"{me}_3pa")
            t["oftm"] += getattr(g, f"{op}_ftm")
            t["ofta"] += getattr(g, f"{op}_fta")
            if abs(us - them) <= 5:
                t["cg"] += 1
                t["cw"] += us > them
        lg["3m"] += g.home_3pm + g.away_3pm
        lg["3a"] += g.home_3pa + g.away_3pa
        lg["ftm"] += g.home_ftm + g.away_ftm
        lg["fta"] += g.home_fta + g.away_fta
    l3 = lg["3m"] / lg["3a"] if lg["3a"] else None
    lft = lg["ftm"] / lg["fta"] if lg["fta"] else None
    raw: dict[str, dict[str, float | None]] = defaultdict(dict)
    for team, t in tot.items():
        exp = 14.0
        pyth = t["pf"] ** exp / (t["pf"] ** exp + t["pa"] ** exp) if t["pf"] and t["pa"] else None
        raw["opp_three_luck"][team] = (t["o3m"] / t["o3a"] - l3) if t["o3a"] and l3 else None
        raw["own_three_vs_league"][team] = (t["3m"] / t["3a"] - l3) if t["3a"] and l3 else None
        raw["opp_ft_luck"][team] = (t["oftm"] / t["ofta"] - lft) if t["ofta"] and lft else None
        raw["close_win_pct"][team] = (t["cw"] / t["cg"]) if t["cg"] >= 3 else None
        raw["wins_over_pythag"][team] = (t["w"] - pyth * t["g"]) if pyth is not None else None
    spec = (("opp_three_luck", "Opponent 3P% Minus League", "low", "pct"),
            ("own_three_vs_league", "Own 3P% Minus League", "high", "pct"),
            ("opp_ft_luck", "Opponent FT% Minus League", "low", "pct"),
            ("close_win_pct", "Win% In Games Decided By 5 Or Fewer", "high", "pct"),
            ("wins_over_pythag", "Wins Above Point-Differential Wins", "high", "signed"))
    out: dict[str, dict] = defaultdict(dict)
    for key, label, better, fmt in spec:
        for team, entry in ranked(raw[key], label, better, fmt).items():
            out[team][key] = entry
    for team, t in tot.items():
        out[team]["games"] = int(t["g"])
        out[team]["close_record"] = f"{int(t['cw'])}-{int(t['cg'] - t['cw'])}"
    return dict(out)


# -- players ----------------------------------------------------------------------

def _by_name(rows: Iterable[dict], key: str = "PLAYER_NAME") -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        out[bbref.norm(r.get(key, ""))].append(r)
    return out


def player_profiles(t: Tables, rotation: list[dict], career_three: dict[str, tuple[int, int]],
                    curve: aging.Curve, injuries: dict[str, str]) -> list[dict]:
    bio = _bio(t)
    syn = {pt: _by_name(t.rows(f"synergy_P_offensive_{pt}")) for pt in nbastats.PLAY_TYPES}
    shots = _by_name(t.rows("shots_Player_Base"))
    speed = _by_name(t.rows("tracking_Player_SpeedDistance"))
    drives = _by_name(t.rows("tracking_Player_Drives"))
    dfn = {cat: _by_name(t.rows(f"defense_{cat.replace(' ', '_')}")) for cat, *_ in DEF_ZONES}
    out = []
    for p in rotation:
        name = p.get("player_name", "")
        key = bbref.norm(name)
        b = (bio.get(key) or {})
        age = _f(b.get("AGE"))
        d_off, d_def = curve.delta(age)
        prof: dict[str, Any] = {
            "player_id": p.get("player_id"), "player_name": name, "position": p.get("position"),
            "headshot": p.get("headshot"), "status": injuries.get(p.get("player_id") or ""),
            "mpg": p.get("mpg"), "ppg": p.get("ppg"),
            "height_in": _f(b.get("PLAYER_HEIGHT_INCHES")), "height": b.get("PLAYER_HEIGHT"),
            "weight_lb": _f(b.get("PLAYER_WEIGHT")), "age": age,
            "usage": _f(b.get("USG_PCT")), "ts": _f(b.get("TS_PCT")),
            "age_change": {"offense": d_off, "defense": d_def,
                           "about": "League history: average change in box impact from this age to the next"},
        }
        zones = []
        srow = (shots.get(key) or [{}])[0]
        total = sum(_f(srow.get(f"{src}|FGA")) or 0 for _, src, _ in ZONES)
        for zk, src, label in ZONES:
            fga = _f(srow.get(f"{src}|FGA"))
            if fga is None:
                continue
            zones.append({"zone": zk, "label": label, "fga": round(fga, 2),
                          "share": round(fga / total, 4) if total else None, "fg": _f(srow.get(f"{src}|FG_PCT"))})
        prof["zones"] = zones
        pts = []
        for pt in nbastats.PLAY_TYPES:
            r = (syn[pt].get(key) or [None])[0]
            if r and (_f(r.get("POSS_PCT")) or 0) >= 0.05:
                pts.append({"play_type": pt, "label": nbastats.PLAY_TYPE_LABEL[pt], "freq": _f(r.get("POSS_PCT")),
                            "ppp": _f(r.get("PPP")), "percentile": _f(r.get("PERCENTILE")),
                            "poss": _f(r.get("POSS"))})
        prof["play_types"] = sorted(pts, key=lambda x: -(x["freq"] or 0))[:5]
        sp = (speed.get(key) or [{}])[0]
        dr = (drives.get(key) or [{}])[0]
        prof["tracking"] = {"speed": _f(sp.get("AVG_SPEED")), "speed_off": _f(sp.get("AVG_SPEED_OFF")),
                            "speed_def": _f(sp.get("AVG_SPEED_DEF")), "miles": _f(sp.get("DIST_MILES")),
                            "drives": _f(dr.get("DRIVES")), "drive_fg": _f(dr.get("DRIVE_FG_PCT"))}
        dz = []
        for cat, col, norm_col, att_col, label in DEF_ZONES:
            r = (dfn[cat].get(key) or [None])[0]
            if not r:
                continue
            d_pct, n_pct, att = _f(r.get(col)), _f(r.get(norm_col)), _f(r.get(att_col))
            if d_pct is None or n_pct is None:
                continue
            dz.append({"zone": cat, "label": label, "fg_against": d_pct, "normal_fg": n_pct,
                       "diff": round(d_pct - n_pct, 4), "attempts_pg": att})
        prof["defense_zones"] = dz
        made, att = career_three.get(p.get("player_id") or "", (0, 0))
        prof["three_track_record"] = {"made": made, "attempts": att,
                                      "pct": round(made / att, 4) if att else None,
                                      "about": "Three-point record over the previous seasons in the box-score cache"}
        out.append(prof)
    return out


def career_threes(season: int, seasons_back: int = 4) -> dict[str, tuple[int, int]]:
    out: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for s in range(season - seasons_back, season):
        for r in ingest.load_players(s):
            pid = r.get("athlete_id")
            if pid:
                out[pid][0] += int(r.get("tpm") or 0)
                out[pid][1] += int(r.get("tpa") or 0)
    return {k: (v[0], v[1]) for k, v in out.items()}


def build_all(season: int, form: dict[str, dict], rotations: dict[str, list[dict]],
              injuries: dict[str, dict[str, str]], espn_coaches: tuple[dict, dict] | None = None
              ) -> tuple[dict[str, dict], dict[str, Any]]:
    """(team -> profile, provenance). ``form`` is features.team_form for the basis season."""
    t = basis_tables(season)
    t_prev = Tables(t.season - 1)
    shots = shot_profile(t)
    pts = play_types(t)
    trk = tracking(t)
    kinds = espn_shot_kinds(t.season) or espn_shot_kinds(season)
    sz = size(t, rotations)
    ec = espn_coaches or ({}, {})
    co = coaches(Tables(season), t if t.season != season else t_prev, ec[0], ec[1])
    # Tenure, career and philosophy from Basketball-Reference's season coaching
    # pages (coach_profiles.py) take precedence: it is the only source that
    # answers a past season with that season's coach.
    from . import coaches as coach_profiles
    from . import schemes
    try:
        bb = coach_profiles.profile_all(season)
    except Exception:  # the ESPN/league staff read stands
        bb = {}
    for team, info in bb.items():
        co[team] = {**(co.get(team) or {}), **info,
                    "head_coach_seasons": info.get("seasons_as_head_coach") or (co.get(team) or {}).get(
                        "head_coach_seasons")}
    try:
        scheme_reads = schemes.reads(t.season)
    except Exception:
        scheme_reads = {}
    reg_season = season if ingest.load_games(season) and max(
        (r.get("games", 0) for r in regression(ingest.load_games(season)).values()), default=0) >= MIN_GAMES \
        else season - 1
    reg = regression(ingest.load_games(reg_season))
    curve = aging.curve()
    c3 = career_threes(t.season)  # seasons before the one the rotation's 3P% is read on
    # Shot zones and on-floor defense come from ESPN play-by-play, which every
    # build refreshes: this season's once clubs have played MIN_GAMES, else
    # last season's. stats.nba.com zones stand in only when ESPN has none.
    zone_season = season if espn_games_played(season) >= MIN_GAMES else season - 1
    espn_zones = espn_team_zones(zone_season)
    espn_pzones = espn_player_zones(zone_season)
    onfloor = espn_onfloor_defense(zone_season)
    if espn_zones:
        shots = espn_zones
    out = {}
    for team in teams.all_abbrs():
        out[team] = {
            "basis": season_label(t.season) + (" Season" if t.season != season else ""),
            "season": t.season,
            "shot_profile": shots.get(team), "shot_kinds": kinds.get(team), "play_types": pts.get(team),
            "tracking": trk.get(team), "size": sz.get(team), "coach": co.get(team),
            "style": style(team, shots, pts, kinds, form),
            "defense_scheme": scheme_reads.get(team),
            "regression": reg.get(team), "regression_basis": season_label(reg_season),
            "players": player_profiles(t, rotations.get(team, [])[:9], c3, curve, injuries.get(team, {})),
            "zones_basis": season_label(zone_season) + " · ESPN Play-By-Play" if espn_zones else None,
        }
        for prof in out[team]["players"]:
            pid = prof.get("player_id") or ""
            if espn_pzones.get(pid):
                prof["zones"] = espn_pzones[pid]
            prof["defense_onfloor"] = onfloor.get(pid, [])
    prov = {"stats_nba_season": season_label(t.season), "stats_nba_as_of": t.as_of(),
            "aging_curve": [{"age": a, "offense": curve.off[a], "defense": curve.deff[a]} for a in sorted(curve.off)],
            "sources": ["stats.nba.com (Synergy play types, Second Spectrum tracking, defender dashboard, "
                        "shot locations, bio, coaches)", "ESPN play-by-play (shot kinds, transition)",
                        "Basketball-Reference (aging curve)"]}
    return out, prov


# -- ESPN play-by-play zones (the automated path: no stats.nba.com needed) -------

ESPN_ZONES = (("rim", "At The Rim"), ("paint", "Paint (Non-RA)"), ("mid", "Mid-Range"),
              ("corner3", "Corner Three"), ("above3", "Above-The-Break Three"))
DEF_GROUPS = (("rim", ("rim",), "At The Rim"), ("paint", ("paint",), "Paint"), ("mid", ("mid",), "Mid-Range"),
              ("three", ("corner3", "above3"), "Three-Pointers"))


def _shots_by_team(season: int):
    games = {g.event_id: g for g in ingest.load_games(season)}
    for r in bulk.load_shots(season):
        g = games.get(r["event_id"])
        if not g or r["zone"] == "heave":
            continue
        off = g.home if r["side"] == "home" else g.away
        dfn = g.away if r["side"] == "home" else g.home
        yield off, dfn, r


def espn_team_zones(season: int) -> dict[str, dict]:
    """Team shot profile (own and allowed) from ESPN shots, in shot_profile()'s shape."""
    tally: dict[str, dict[str, dict[str, list[int]]]] = defaultdict(
        lambda: {"offense": defaultdict(lambda: [0, 0]), "defense": defaultdict(lambda: [0, 0])})
    for off, dfn, r in _shots_by_team(season):
        made = int(r["made"])
        for team, side in ((off, "offense"), (dfn, "defense")):
            t = tally[team][side][r["zone"]]
            t[0] += 1
            t[1] += made
    out: dict[str, dict] = defaultdict(dict)
    for side in ("offense", "defense"):
        for key, label in ESPN_ZONES:
            share, pct = {}, {}
            for team, sides in tally.items():
                total = sum(v[0] for v in sides[side].values())
                att, made = sides[side][key]
                share[team] = att / total if total else None
                pct[team] = made / att if att >= 20 else None
            who = "" if side == "offense" else " Allowed"
            s_r = ranked(share, f"{label} Share Of Shots{who}", "neutral", "pct")
            p_r = ranked(pct, f"{label} FG%{who}", "high" if side == "offense" else "low", "pct")
            for team in tally:
                out[team].setdefault(side, {})[key] = {"label": label, "share": s_r.get(team), "fg": p_r.get(team)}
    return dict(out)


def espn_player_zones(season: int) -> dict[str, list[dict]]:
    """ESPN athlete id -> zones like player_profiles()' (share, FG%, attempts per game)."""
    tally: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    games: dict[str, set] = defaultdict(set)
    for _, _, r in _shots_by_team(season):
        t = tally[r["shooter"]][r["zone"]]
        t[0] += 1
        t[1] += int(r["made"])
        games[r["shooter"]].add(r["event_id"])
    out = {}
    for pid, z in tally.items():
        total = sum(v[0] for v in z.values())
        n = len(games[pid]) or 1
        out[pid] = [{"zone": key, "label": label, "fga": round(z[key][0] / n, 2),
                     "share": round(z[key][0] / total, 4) if total else None,
                     "fg": round(z[key][1] / z[key][0], 4) if z[key][0] else None}
                    for key, label in ESPN_ZONES if z[key][0]]
    return out


def espn_onfloor_defense(season: int, min_att: int = 60) -> dict[str, list[dict]]:
    """ESPN athlete id -> opponents' FG% by zone with him on the floor against with him off it.

    On/off is noisy and shares credit with four teammates; it is shown as
    what it is, with the attempts behind each figure, and only above
    ``min_att`` attempts on each side of the split.
    """
    team_zone: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    on: dict[tuple[str, str], dict[str, list[int]]] = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    group_of = {z: g for g, zones, _ in DEF_GROUPS for z in zones}
    for _, dfn, r in _shots_by_team(season):
        grp = group_of.get(r["zone"])
        if not grp:
            continue
        made = int(r["made"])
        t = team_zone[dfn][grp]
        t[0] += 1
        t[1] += made
        for pid in r["defense"].split("|"):
            o = on[(pid, dfn)][grp]
            o[0] += 1
            o[1] += made
    out: dict[str, list[dict]] = {}
    for (pid, team), zones in on.items():
        rows = []
        for grp, _, label in DEF_GROUPS:
            a_on, m_on = zones[grp]
            a_all, m_all = team_zone[team][grp]
            a_off, m_off = a_all - a_on, m_all - m_on
            if a_on < min_att or a_off < min_att:
                continue
            fg_on, fg_off = m_on / a_on, m_off / a_off
            rows.append({"zone": grp, "label": label, "fg_on": round(fg_on, 4), "fg_off": round(fg_off, 4),
                         "diff": round(fg_on - fg_off, 4), "attempts_on": a_on, "attempts_off": a_off})
        # A player traded mid-season keeps the stint he defended most.
        if rows and sum(r["attempts_on"] for r in rows) > sum(r["attempts_on"] for r in out.get(pid, [])):
            out[pid] = rows
    return out


def espn_games_played(season: int) -> float:
    counts: dict[str, int] = defaultdict(int)
    for g in ingest.load_games(season):
        counts[g.home] += 1
        counts[g.away] += 1
    vals = sorted(counts.values())
    return vals[len(vals) // 2] if vals else 0.0
