"""Lineup stints from ESPN play-by-play.

A stint is a stretch of a game with the same ten players on the floor. For
each one this module records both lineups, the points each side scored and the
possessions each side used, which is the input to regularized adjusted
plus-minus (rapm.py).

ESPN logs in-period substitutions ("X enters the game for Y") but not the
lineups that start the second, third and fourth quarters or overtime. A
period's opening five are therefore inferred the standard way: everyone who
acts in the period before being subbed in, plus everyone subbed out before
being subbed in, topped up from the previous period's closing five if a player
stood on the floor without touching the ball. Stints whose lineups cannot be
resolved to five a side are dropped, never guessed.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

SHOT_WORDS = ("shot", "layup", "dunk", "jumper", "hook", "tip", "fadeaway", "bank", "finger roll", "alley")


@dataclass
class Stint:
    event_id: str
    home_players: tuple[str, ...]
    away_players: tuple[str, ...]
    home_pts: int
    away_pts: int
    home_fga: int
    away_fga: int
    home_fta: int
    away_fta: int
    home_oreb: int
    away_oreb: int
    home_tov: int
    away_tov: int
    seconds: float

    def possessions(self) -> float:
        h = self.home_fga - self.home_oreb + self.home_tov + 0.44 * self.home_fta
        a = self.away_fga - self.away_oreb + self.away_tov + 0.44 * self.away_fta
        return max(0.0, 0.5 * (h + a))


@dataclass
class Shot:
    """One field-goal attempt with the ten players on the floor when it went up."""
    event_id: str
    side: str                  # home | away (the shooting side)
    shooter: str
    assister: str
    zone: str                  # rim | paint | mid | corner3 | above3 | heave
    kind: str                  # see shot_kind()
    value: int                 # 2 or 3
    made: int
    distance: float
    x: float
    y: float
    transition: int            # within TRANSITION_SECONDS of winning the ball
    offense: tuple[str, ...]
    defense: tuple[str, ...]


TRANSITION_SECONDS = 8.0
DISTANCE = re.compile(r"(\d+)-foot")


def shot_zone(value: int, distance: float, x: float, y: float) -> str:
    """The league's zones from ESPN coordinates (x 0-50 across, y feet from the basket line)."""
    if distance >= 40:
        return "heave"
    if value == 3:
        return "corner3" if y <= 8 else "above3"
    if distance <= 4:
        return "rim"
    if abs(x - 25) <= 8 and y <= 14:
        return "paint"
    return "mid"


def shot_kind(type_text: str, assisted: bool, value: int, distance: float) -> str:
    """ESPN credits assists only on makes, so assisted jumpers are not a kind of their own."""
    t = type_text.lower()
    if "putback" in t or t.startswith("tip"):
        return "putback"
    if "cutting" in t or "alley" in t:
        return "cut"
    if "hook" in t or "turnaround" in t or "post" in t:
        return "post"
    if "float" in t:
        return "floater"
    if "step back" in t or "stepback" in t:
        return "stepback"
    if "pullup" in t or "pull-up" in t or "pull up" in t:
        return "pullup"
    if "driving" in t or "running" in t and ("layup" in t or "dunk" in t or "finger" in t):
        return "drive"
    if "dunk" in t or "layup" in t or "finger roll" in t:
        return "rim_finish"
    return "jumper"


def _clock_seconds(play: dict[str, Any]) -> float:
    period = int((play.get("period") or {}).get("number") or 1)
    raw = str((play.get("clock") or {}).get("displayValue") or "0:00")
    if ":" in raw:
        m, s = raw.split(":", 1)
        left = int(m) * 60 + float(s)
    else:
        left = float(raw or 0)
    length = 720 if period <= 4 else 300
    start = 4 * 720 + (period - 5) * 300 if period > 4 else (period - 1) * 720
    return start + (length - left)


def _kind(play: dict[str, Any]) -> str:
    text = str((play.get("type") or {}).get("text") or "").lower()
    if text == "substitution":
        return "sub"
    if "free throw" in text:
        return "ft"
    if "offensive rebound" in text:
        return "oreb"
    if "defensive rebound" in text:
        return "dreb"
    if "turnover" in text or "traveling" in text or "offensive foul" in text and "turnover" in text:
        return "tov"
    if play.get("shootingPlay") and "free throw" not in text:
        return "fga"
    if any(w in text for w in SHOT_WORDS):
        return "fga"
    return "other"


def parse(event_id: str, data: dict[str, Any]) -> list[Stint]:
    return parse_all(event_id, data)[0]


def parse_all(event_id: str, data: dict[str, Any]) -> tuple[list[Stint], list[Shot]]:
    comp = ((data.get("header") or {}).get("competitions") or [{}])[0]
    side_of: dict[str, str] = {}
    for c in comp.get("competitors", []):
        side_of[str(c.get("id") or (c.get("team") or {}).get("id"))] = c.get("homeAway")
    if set(side_of.values()) != {"home", "away"}:
        return [], []
    starters: dict[str, set[str]] = {"home": set(), "away": set()}
    roster: dict[str, str] = {}
    for block in (data.get("boxscore") or {}).get("players", []):
        side = side_of.get(str((block.get("team") or {}).get("id")))
        if not side:
            continue
        for stat in block.get("statistics", [])[:1]:
            for ath in stat.get("athletes", []):
                pid = str((ath.get("athlete") or {}).get("id") or "")
                if not pid:
                    continue
                roster[pid] = side
                if ath.get("starter"):
                    starters[side].add(pid)
    plays = sorted(data.get("plays") or [], key=lambda p: int(p.get("sequenceNumber") or 0))
    if not plays or len(starters["home"]) != 5 or len(starters["away"]) != 5:
        return [], []

    by_period: dict[int, list[dict[str, Any]]] = {}
    for p in plays:
        by_period.setdefault(int((p.get("period") or {}).get("number") or 1), []).append(p)

    def opening(period_plays, prev):
        on = {"home": set(), "away": set()}
        entered: set[str] = set()
        for p in period_plays:
            parts = [str((x.get("athlete") or {}).get("id") or "") for x in p.get("participants") or []]
            if _kind(p) == "sub" and len(parts) >= 2:
                incoming, outgoing = parts[0], parts[1]
                if outgoing not in entered and outgoing in roster:
                    on[roster[outgoing]].add(outgoing)
                entered.add(incoming)
                continue
            for pid in parts:
                if pid in roster and pid not in entered:
                    on[roster[pid]].add(pid)
        for side in ("home", "away"):
            if len(on[side]) < 5:
                for pid in prev[side]:
                    if len(on[side]) >= 5:
                        break
                    if pid not in entered:
                        on[side].add(pid)
        return on

    out: list[Stint] = []
    shots: list[Shot] = []
    won_ball: dict[str, float] = {"home": -1e9, "away": -1e9}
    prev = {k: set(v) for k, v in starters.items()}
    score = {"home": 0, "away": 0}
    for period in sorted(by_period):
        pp = by_period[period]
        on = {k: set(v) for k, v in starters.items()} if period == 1 else opening(pp, prev)
        acc = None

        def flush(end_t):
            # Free throws after a substitution share its clock time: a stint
            # that lasted no time but carries events is kept.
            busy = any(acc[f"{s}_{k}"] for s in ("home", "away") for k in ("pts", "fga", "fta", "tov"))
            if acc and len(acc["home"]) == 5 and len(acc["away"]) == 5 and (end_t > acc["t0"] or busy):
                out.append(Stint(event_id, tuple(sorted(acc["home"])), tuple(sorted(acc["away"])),
                                 acc["home_pts"], acc["away_pts"], acc["home_fga"], acc["away_fga"],
                                 acc["home_fta"], acc["away_fta"], acc["home_oreb"], acc["away_oreb"],
                                 acc["home_tov"], acc["away_tov"], end_t - acc["t0"]))

        def start(t0):
            return {"t0": t0, "home": set(on["home"]), "away": set(on["away"]),
                    **{f"{s}_{k}": 0 for s in ("home", "away") for k in ("pts", "fga", "fta", "oreb", "tov")}}

        t_start = _clock_seconds({"period": {"number": period}, "clock": {"displayValue": "12:00" if period <= 4
                                                                          else "5:00"}})
        acc = start(t_start)
        last_t = t_start
        for p in pp:
            # Points come from the running score, which cannot drift the way a
            # per-play scoreValue can (it is missing on some made free throws).
            hs, as_ = p.get("homeScore"), p.get("awayScore")
            if hs is not None and as_ is not None:
                hs, as_ = int(hs), int(as_)
                acc["home_pts"] += max(0, hs - score["home"])
                acc["away_pts"] += max(0, as_ - score["away"])
                score["home"], score["away"] = max(score["home"], hs), max(score["away"], as_)
            t = _clock_seconds(p)
            last_t = max(last_t, t)
            kind = _kind(p)
            side = side_of.get(str((p.get("team") or {}).get("id")))
            if kind == "sub":
                parts = [str((x.get("athlete") or {}).get("id") or "") for x in p.get("participants") or []]
                if len(parts) < 2 or parts[0] not in roster:
                    continue
                s = roster[parts[0]]
                flush(t)
                on[s].discard(parts[1])
                on[s].add(parts[0])
                acc = start(t)
                continue
            if not side:
                continue
            # Team rebounds (no player credited) are not in the box score's
            # offensive rebounds and would inflate possessions.
            if kind in ("oreb", "dreb") and not p.get("participants"):
                continue
            if kind == "dreb":
                won_ball[side] = t
            elif kind == "tov":
                won_ball["away" if side == "home" else "home"] = t
            if kind == "fga":
                acc[f"{side}_fga"] += 1
                parts = [str((x.get("athlete") or {}).get("id") or "") for x in p.get("participants") or []]
                text = str(p.get("text") or "")
                value = 3 if int(p.get("pointsAttempted") or 0) == 3 or "three point" in text.lower() else 2
                coord = p.get("coordinate") or {}
                cx, cy = float(coord.get("x", -1)), float(coord.get("y", -1))
                m = DISTANCE.search(text)
                if m:
                    dist = float(m.group(1))
                elif 0 <= cx <= 50 and cy >= 0:
                    dist = math.hypot(cx - 25.0, cy)
                else:
                    dist = 0.0 if value == 2 else 24.0
                if not (0 <= cx <= 50 and 0 <= cy <= 94):
                    cx, cy = 25.0, max(0.0, dist)
                assisted = "assist" in text.lower() and len(parts) > 1
                other = "away" if side == "home" else "home"
                if len(on[side]) == 5 and len(on[other]) == 5 and parts and parts[0]:
                    shots.append(Shot(event_id, side, parts[0], parts[1] if assisted else "",
                                      shot_zone(value, dist, cx, cy),
                                      shot_kind(str((p.get("type") or {}).get("text") or ""), assisted, value, dist),
                                      value, 1 if p.get("scoringPlay") else 0, dist, cx, cy,
                                      1 if t - won_ball[side] <= TRANSITION_SECONDS else 0,
                                      tuple(sorted(on[side])), tuple(sorted(on[other]))))
            elif kind == "ft":
                acc[f"{side}_fta"] += 1
            elif kind == "oreb":
                acc[f"{side}_oreb"] += 1
            elif kind == "tov":
                acc[f"{side}_tov"] += 1
        end = t_start + (720 if period <= 4 else 300)
        flush(max(end, last_t))
        prev = {k: set(v) for k, v in on.items()}
    return out, shots
