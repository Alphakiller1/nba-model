"""Who plays: a club's rating adjusted for the rotation it actually has tonight.

A team rating describes the rotation that produced its recent games. When a
regular sits, or one who missed those games returns, tonight's club differs
from that rating. Each player carries a per-minute value (Game Score per
minute, shrunk toward replacement level by minutes played). A club's
*rotation value* is the minutes-share-weighted sum of its players' values:

* recent  - over its last ``WINDOW`` games, absences counted as zero minutes;
* tonight - the same players' shares when they play, restricted to those
  available, capped so the shares fill at most one floor's worth of minutes.

The adjustment is ``AVAIL_SCALE * (tonight - recent)`` points per 100
possessions on the club's net rating, split evenly between offense and
defense. ``AVAIL_SCALE`` is fitted on past seasons (scripts/tune.py).

Backtests read availability from the box score (who played); live boards read
it from ESPN's injury report. The second is noisier, and the methodology says
so.
"""
from __future__ import annotations

from bisect import bisect_left
from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable, Sequence

from .ingest import Game

WINDOW = 10
SHRINK_MINUTES = 250.0
REPLACEMENT = 0.20  # Game Score per minute of a replacement-level player


@dataclass(frozen=True)
class Line:
    event_id: str
    team: str
    player: str
    name: str
    minutes: float
    gmsc: float


def game_score(r: dict[str, str]) -> float:
    f = {k: float(r.get(k) or 0) for k in ("pts", "fgm", "fga", "ftm", "fta", "reb", "ast",
                                           "stl", "blk", "tov")}
    return (f["pts"] + 0.4 * f["fgm"] - 0.7 * f["fga"] - 0.4 * (f["fta"] - f["ftm"])
            + 0.45 * f["reb"] + 0.7 * f["ast"] + f["stl"] + 0.7 * f["blk"] - f["tov"])


def lines(rows: Iterable[dict[str, str]]) -> list[Line]:
    out = []
    for r in rows:
        m = float(r.get("minutes") or 0)
        if m <= 0:
            continue
        out.append(Line(r["event_id"], r["team"], r["athlete_id"], r.get("name", ""), m, game_score(r)))
    return out


class Tracker:
    """Walks a season in date order and answers 'what is this club's rotation'.

    ``prior`` lines (last season) seed each player's value so opening night is
    not valued at replacement level for everyone.
    """

    def __init__(self, games: Sequence[Game], season_lines: Sequence[Line],
                 prior_lines: Sequence[Line] = ()):
        self.order = {g.event_id: i for i, g in enumerate(sorted(games, key=lambda g: g.date_utc))}
        self.by_event: dict[str, list[Line]] = defaultdict(list)
        for ln in season_lines:
            self.by_event[ln.event_id].append(ln)
        self.team_games: dict[str, list[str]] = defaultdict(list)
        for g in sorted(games, key=lambda g: g.date_utc):
            self.team_games[g.home].append(g.event_id)
            self.team_games[g.away].append(g.event_id)
        self.prior_min: dict[str, float] = defaultdict(float)
        self.prior_gs: dict[str, float] = defaultdict(float)
        for ln in prior_lines:
            self.prior_min[ln.player] += ln.minutes
            self.prior_gs[ln.player] += ln.gmsc

    def _value(self, player: str, upto: int) -> float:
        idxs, cum_min, cum_gs = self._cum.get(player, ((), (), ()))
        k = bisect_left(idxs, upto)
        mins = cum_min[k - 1] if k else 0.0
        gs = cum_gs[k - 1] if k else 0.0
        # Last season's production counts at half weight.
        mins += 0.5 * self.prior_min.get(player, 0.0)
        gs += 0.5 * self.prior_gs.get(player, 0.0)
        return (gs + REPLACEMENT * SHRINK_MINUTES) / (mins + SHRINK_MINUTES) - REPLACEMENT

    def prepare(self) -> "Tracker":
        played: dict[str, list[tuple[int, float, float]]] = defaultdict(list)
        for eid, rows in self.by_event.items():
            for ln in rows:
                played[ln.player].append((self.order.get(eid, 10 ** 9), ln.minutes, ln.gmsc))
        self._cum: dict[str, tuple[list[int], list[float], list[float]]] = {}
        for p, seq in played.items():
            seq.sort()
            idxs, cm, cg = [], [], []
            m = g = 0.0
            for idx, mins, gs in seq:
                m += mins
                g += gs
                idxs.append(idx)
                cm.append(m)
                cg.append(g)
            self._cum[p] = (idxs, cm, cg)
        return self

    def recent(self, team: str, event_id: str) -> tuple[dict[str, float], dict[str, float], dict[str, str]]:
        """(share over window incl. absences, share when playing, names) before event_id."""
        hist = self.team_games[team]
        i = hist.index(event_id) if event_id in hist else len(hist)
        window = hist[max(0, i - WINDOW):i]
        mins: dict[str, float] = defaultdict(float)
        apps: dict[str, int] = defaultdict(int)
        names: dict[str, str] = {}
        for eid in window:
            for ln in self.by_event[eid]:
                if ln.team != team:
                    continue
                mins[ln.player] += ln.minutes
                apps[ln.player] += 1
                names[ln.player] = ln.name
        n = len(window)
        if not n:
            return {}, {}, {}
        share = {p: m / (240.0 * n) for p, m in mins.items()}
        when = {p: mins[p] / (240.0 * apps[p]) for p in mins}
        return share, when, names

    def adjustment(self, team: str, event_id: str, available: set[str] | None) -> tuple[float, list[dict]]:
        """(tonight - recent) rotation value, and the players driving it.

        ``available`` is the set of player ids expected to play; None means
        "use who actually appeared in this game's box" (backtests).
        """
        share, when, names = self.recent(team, event_id)
        if not share:
            return 0.0, []
        upto = self.order.get(event_id, 10 ** 9)
        if available is None:
            available = {ln.player for ln in self.by_event.get(event_id, ()) if ln.team == team}
        val = {p: self._value(p, upto) for p in share}
        recent = sum(share[p] * val[p] for p in share)
        on = {p: when[p] for p in share if p in available}
        total = sum(on.values())
        scale = 1.0 / total if total > 1.0 else 1.0
        tonight = sum(on[p] * scale * val[p] for p in on)
        movers = []
        for p in share:
            delta = (on.get(p, 0.0) * scale - share[p]) * val[p]
            if abs(delta) >= 0.004:
                movers.append({"player_id": p, "name": names.get(p, ""), "out": p not in available,
                               "minutes_share": round(when[p] * 5, 3), "value": round(val[p], 3),
                               "delta": round(delta, 4)})
        movers.sort(key=lambda m: m["delta"])
        return tonight - recent, movers


# -- roster continuity: last season's production re-weighted to this roster ---

def season_values(prev_lines: Sequence[Line],
                  older_lines: Sequence[Line] = ()) -> dict[str, tuple[float, float]]:
    """player -> (value per minute above replacement, share of team minutes when playing).

    A player with no minutes last season (a lost year: Haliburton missed all
    of 2025-26) takes his line from the season before, so a returning regular
    is not valued at replacement level.
    """
    def collect(rows: Sequence[Line]) -> dict[str, tuple[float, float]]:
        mins: dict[str, float] = defaultdict(float)
        gs: dict[str, float] = defaultdict(float)
        apps: dict[str, int] = defaultdict(int)
        for ln in rows:
            mins[ln.player] += ln.minutes
            gs[ln.player] += ln.gmsc
            apps[ln.player] += 1
        return {p: ((gs[p] + REPLACEMENT * SHRINK_MINUTES) / (mins[p] + SHRINK_MINUTES) - REPLACEMENT,
                    mins[p] / (240.0 * apps[p]))
                for p in mins}

    vals = collect(older_lines)
    vals.update(collect(prev_lines))
    return vals


def last_team_value(prev_lines: Sequence[Line], vals: dict[str, tuple[float, float]]) -> dict[str, float]:
    """Each club's minutes-weighted rotation value over last season."""
    mins: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    events: dict[str, set] = defaultdict(set)
    for ln in prev_lines:
        mins[ln.team][ln.player] += ln.minutes
        events[ln.team].add(ln.event_id)
    return {t: sum(m / (240.0 * len(events[t])) * vals[p][0] for p, m in ps.items())
            for t, ps in mins.items()}


def roster_value(players: Iterable[str], vals: dict[str, tuple[float, float]]) -> float:
    """Rotation value of a set of players at last season's usage, capped at one floor."""
    on = {p: vals[p] for p in players if p in vals}
    total = sum(share for _, share in on.values())
    scale = 1.0 / total if total > 1.0 else 1.0
    return sum(share * scale * value for value, share in on.values())
