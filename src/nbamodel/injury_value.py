"""What an NBA player is worth to the spread (owner 2026-11-13).

Calibrated on closing lines (scripts/injury_value_fit.py): over the 2025-26
games with a DraftKings close, a club's absent regulars moved the line by

    value = role x (minutes / 48) + star x (game score per game)^2 / 10

with ``role`` and ``star`` fitted against a strength per club (held-out
closing-spread RMSE 4.38 -> 3.40). The minutes term is a rotation spot; the
squared production term is why a star's absence moves a line far more than
a role player's. RAPM added nothing once these two were in (its weight fell
to zero), so the value reads only the box score.

Minutes are the player model's expected minutes (his last ten appearances
this season, else 90% of last season's average); game score is his average
over the same appearances, else last season's.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Iterable

from . import availability

FIT = Path(__file__).resolve().parents[2] / "data" / "injury_value_fit.json"
WINDOW = 10


def coefficients() -> dict[str, float]:
    data = json.loads(FIT.read_text(encoding="utf-8"))
    return {"role": float(data["points"]["role"]), "star": float(data["points"]["gmsc2"])}


def value(minutes: float, game_score: float, coef: dict[str, float]) -> float:
    return coef["role"] * minutes / 48.0 + coef["star"] * max(game_score, 0.0) ** 2 / 10.0


class Production:
    """Each player's recent minutes and game score, newest appearances first,
    and each club's games this season in order."""

    def __init__(self, lines: Iterable[dict[str, str]], prev_lines: Iterable[dict[str, str]],
                 order: dict[str, float]):
        def collect(rows, keep_order):
            acc: dict[str, list[tuple[float, float, float]]] = defaultdict(list)
            for r in rows:
                m = float(r.get("minutes") or 0)
                if m <= 0:
                    continue
                acc[r["athlete_id"]].append((keep_order.get(r["event_id"], 0.0), m, availability.game_score(r)))
            return {k: sorted(v)[-WINDOW:] for k, v in acc.items()}
        lines = list(lines)
        self.now = collect(lines, order)
        self.prev = collect(prev_lines, defaultdict(float))
        games: dict[str, set[str]] = defaultdict(set)
        self.played: dict[tuple[str, str], dict[str, tuple[float, float]]] = defaultdict(dict)
        for r in lines:
            team = r.get("team")
            if not team:
                continue
            games[team].add(r["event_id"])
            m = float(r.get("minutes") or 0)
            if m > 0:
                self.played[(team, r["athlete_id"])][r["event_id"]] = (m, availability.game_score(r))
        self.club_games = {t: sorted(ev, key=lambda e: order.get(e, 0.0)) for t, ev in games.items()}

    def recent(self, pid: str, team: str) -> tuple[tuple[float, float] | None, int | None]:
        """What the fit counted when he sat (scripts/injury_value_fit.py): his
        (minutes, game score) per appearance over the club's last ten games
        when he made three or more of them, else None - so a long absence,
        which the line has carried, fades out. And the club's games in a row,
        newest first, without him (None when he has not played for it)."""
        games = self.club_games.get(team, [])
        mine = self.played.get((team, pid), {})
        if not mine:
            return None, None
        missed = next(i for i, e in enumerate(reversed(games)) if e in mine)
        hits = [mine[e] for e in games[-WINDOW:] if e in mine]
        if len(hits) < 3:
            return None, missed
        return (sum(m for m, _ in hits) / len(hits), sum(g for _, g in hits) / len(hits)), missed

    def of(self, pid: str) -> tuple[float, float] | None:
        """(minutes, game score) per game: this season's last ten appearances,
        else last season at 90% of the minutes."""
        rows = self.now.get(pid)
        if rows:
            return sum(m for _, m, _ in rows) / len(rows), sum(g for _, _, g in rows) / len(rows)
        rows = self.prev.get(pid)
        if rows:
            return 0.9 * sum(m for _, m, _ in rows) / len(rows), sum(g for _, _, g in rows) / len(rows)
        return None
