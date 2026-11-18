"""Five-man units and situational splits for the public NBA desk (2026-11-18 audit).

Lineups: each club's most-used five-man units, summed from this repo's own
play-by-play stints (units_from_stints), with minutes and points per 100
possessions on offence and defence, and the net of the two. pbpstats.com
fills only a club the stints do not cover.

Splits: from the games this repo already ingests, each club's net points per
100 possessions at home and on the road, on the second night of a
back-to-back and on a day or more of rest, with the games each rests on,
ranked among the 30 clubs. Possessions are FGA - OREB + TOV + 0.44 FTA.

Everything is observed; nothing is a projection.
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime
from typing import Any, Iterable

PBPSTATS = "https://api.pbpstats.com/get-totals/nba"
HEADERS = {"User-Agent": "ChaseAnalytics/1.0 (+https://chase-analytics.com)"}
NBA_IDS = {
    "ATL": 1610612737, "BOS": 1610612738, "BKN": 1610612751, "CHA": 1610612766, "CHI": 1610612741,
    "CLE": 1610612739, "DAL": 1610612742, "DEN": 1610612743, "DET": 1610612765, "GS": 1610612744,
    "HOU": 1610612745, "IND": 1610612754, "LAC": 1610612746, "LAL": 1610612747, "MEM": 1610612763,
    "MIA": 1610612748, "MIL": 1610612749, "MIN": 1610612750, "NO": 1610612740, "NY": 1610612752,
    "OKC": 1610612760, "ORL": 1610612753, "PHI": 1610612755, "PHX": 1610612756, "POR": 1610612757,
    "SAC": 1610612758, "SA": 1610612759, "TOR": 1610612761, "UTAH": 1610612762, "WSH": 1610612764,
}
TOP_UNITS = 5
MIN_UNIT_MINUTES = 15


def season_label(season: int) -> str:
    """2027 -> "2026-27", pbpstats' season name."""
    return f"{season - 1}-{str(season)[2:]}"


def _fetch(url: str) -> dict:
    with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


def _per100(points: float, poss: float) -> float | None:
    return round(100.0 * points / poss, 1) if poss else None


def units(rows: list[dict]) -> list[dict[str, Any]]:
    """The most-used five-man units from one club's pbpstats lineup rows."""
    out = []
    for r in sorted(rows, key=lambda r: -float(r.get("Minutes") or 0)):
        minutes = float(r.get("Minutes") or 0)
        if minutes < MIN_UNIT_MINUTES:
            break
        off = _per100(float(r.get("Points") or 0), float(r.get("OffPoss") or 0))
        de = _per100(float(r.get("OpponentPoints") or 0), float(r.get("DefPoss") or 0))
        out.append({"players": [p.strip() for p in str(r.get("Name") or "").split(",") if p.strip()],
                    "minutes": round(minutes, 1), "off_poss": int(r.get("OffPoss") or 0),
                    "off_rating": off, "def_rating": de,
                    "net_rating": round(off - de, 1) if off is not None and de is not None else None})
        if len(out) == TOP_UNITS:
            break
    return out


def fetch_lineups(season: int, clubs: Iterable[str], fetch=_fetch, pause: float = 2.0,
                  give_up_after: int = 2) -> dict[str, list[dict]]:
    """{club: [unit, ...]} for each club pbpstats answers for; an unreachable
    club is left out, never filled in.

    pbpstats answers a slow trickle and stops answering a quick run (every
    request after the third timed out on 2026-11-18), so requests are two
    seconds apart, and after ``give_up_after`` clubs in a row fail the rest
    are skipped: an outage costs the build a minute, not half an hour."""
    out: dict[str, list[dict]] = {}
    failed_in_a_row = 0
    for club in sorted(set(clubs)):
        if club not in NBA_IDS:
            continue
        if failed_in_a_row >= give_up_after:
            print(f"  lineups: pbpstats stopped answering; {club} and later clubs skipped")
            break
        query = urllib.parse.urlencode({"Season": season_label(season), "SeasonType": "Regular Season",
                                        "Type": "Lineup", "TeamId": NBA_IDS[club]})
        payload = None
        for attempt in (1, 2):  # one retry: pbpstats answers an occasional 502
            try:
                payload = fetch(f"{PBPSTATS}?{query}")
                break
            except Exception as error:  # noqa: BLE001 - one club failing leaves only that club out
                if attempt == 2:
                    print(f"  lineups {club}: unavailable ({error})")
                elif fetch is _fetch:
                    time.sleep(3)
        if fetch is _fetch:
            time.sleep(pause)
        if payload is None:
            failed_in_a_row += 1
            continue
        failed_in_a_row = 0
        found = units(payload.get("multi_row_table_data") or [])
        if found:
            out[club] = found
    return out


def _poss(fga: float, oreb: float, tov: float, fta: float) -> float:
    return fga - oreb + tov + 0.44 * fta


def units_from_stints(stints: Iterable[dict], games: Iterable[Any], players: Iterable[dict]) -> dict[str, list[dict]]:
    """{club: [unit, ...]} from this repo's own play-by-play stints
    (bulk.load_stints): every stretch a five was on the floor, summed. No
    third-party call, so the units cannot go missing with pbpstats (which
    stopped answering on 2026-11-18); the figures match its definitions
    (points per 100 possessions both ways, FGA - OREB + TOV + 0.44 FTA)."""
    side_of = {str(g.event_id): (g.home, g.away) for g in games}
    names = {str(r.get("athlete_id")): r.get("name") for r in players if r.get("athlete_id")}
    acc: dict[tuple[str, tuple[str, ...]], list[float]] = defaultdict(lambda: [0.0] * 5)

    def num(row, key):
        try:
            return float(row.get(key) or 0)
        except (TypeError, ValueError):
            return 0.0
    for r in stints:
        teams = side_of.get(str(r.get("event_id")))
        if not teams:
            continue
        for side, other, club in (("home", "away", teams[0]), ("away", "home", teams[1])):
            five = tuple(sorted(p for p in str(r.get(f"{side}_players") or "").split("|") if p))
            if len(five) != 5:
                continue
            a = acc[(club, five)]
            a[0] += num(r, "seconds")
            a[1] += num(r, f"{side}_pts")
            a[2] += num(r, f"{other}_pts")
            a[3] += _poss(num(r, f"{side}_fga"), num(r, f"{side}_oreb"), num(r, f"{side}_tov"), num(r, f"{side}_fta"))
            a[4] += _poss(num(r, f"{other}_fga"), num(r, f"{other}_oreb"), num(r, f"{other}_tov"),
                          num(r, f"{other}_fta"))
    by_club: dict[str, list[dict]] = defaultdict(list)
    for (club, five), (secs, pts, opp, poss, opp_poss) in acc.items():
        minutes = secs / 60
        if minutes < MIN_UNIT_MINUTES:
            continue
        off, de = _per100(pts, poss), _per100(opp, opp_poss)
        by_club[club].append({"players": [names.get(p) or p for p in five], "minutes": round(minutes, 1),
                              "off_poss": int(round(poss)), "off_rating": off, "def_rating": de,
                              "net_rating": round(off - de, 1) if off is not None and de is not None else None})
    return {club: sorted(units, key=lambda u: -u["minutes"])[:TOP_UNITS] for club, units in by_club.items()}


def splits(games: Iterable[Any]) -> dict[str, dict[str, Any]]:
    """{club: {split: ranked entry, ..., "games": {split: n}}} over the season's games."""
    played: dict[str, list[tuple[datetime, Any, str]]] = defaultdict(list)
    for g in games:
        try:
            when = datetime.fromisoformat(str(g.date_utc).replace("Z", "+00:00"))
        except ValueError:
            continue
        played[g.home].append((when, g, "home"))
        played[g.away].append((when, g, "away"))
    tally: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0, 0.0, 0]))
    for club, rows in played.items():
        rows.sort(key=lambda r: r[0])
        last = None
        for when, g, side in rows:
            other = "away" if side == "home" else "home"
            pts, opp = getattr(g, f"{side}_pts"), getattr(g, f"{other}_pts")
            poss = (_poss(getattr(g, f"{side}_fga"), getattr(g, f"{side}_oreb"), getattr(g, f"{side}_tov"),
                          getattr(g, f"{side}_fta")) +
                    _poss(getattr(g, f"{other}_fga"), getattr(g, f"{other}_oreb"), getattr(g, f"{other}_tov"),
                          getattr(g, f"{other}_fta"))) / 2
            rest = (when.date() - last.date()).days - 1 if last else None
            last = when
            keys = ["at_home" if side == "home" and not g.neutral else "on_road"]
            if rest is not None:
                keys.append("back_to_back" if rest <= 0 else "rested")
            for key in keys:
                t = tally[club][key]
                t[0] += pts
                t[1] += opp
                t[2] += poss
                t[3] += 1
    labels = {"at_home": "Net Rating At Home", "on_road": "Net Rating On The Road",
              "back_to_back": "Net Rating, Second Night Of A Back-To-Back", "rested": "Net Rating With Rest"}
    nets = {key: {club: round(100 * (t[key][0] - t[key][1]) / t[key][2], 1)
                  for club, t in tally.items() if t[key][3] and t[key][2]} for key in labels}
    out: dict[str, dict[str, Any]] = defaultdict(lambda: {"games": {}})
    for key, values in nets.items():
        pool = list(values.values())
        for club, value in values.items():
            out[club][key] = {"label": labels[key], "value": value, "better": "high",
                              "rank": 1 + sum(1 for v in pool if v > value), "of": len(pool), "format": "signed"}
    for club, t in tally.items():
        out[club]["games"] = {key: int(t[key][3]) for key in labels if t[key][3]}
    return dict(out)
