"""Tonight's slate as ESPN publishes it: fixtures, quotes, injuries, rosters.

The slate date is the US Eastern day of the next game still to tip, so the
board rolls forward once every game of a night is final (or in progress past
the last tip of the night). Nothing here is computed; it is the source record
the board and the public slate are both built from.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from . import espn, teams

try:
    from zoneinfo import ZoneInfo

    ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover - Windows without tzdata
    ET = timezone(timedelta(hours=-4))

ATHLETE_ID = re.compile(r"/id/(\d+)")


@dataclass
class Fixture:
    event_id: str
    tip_utc: str
    state: str                 # scheduled | live | final
    status_detail: str
    home: str
    away: str
    neutral: bool
    venue: str
    venue_city: str
    broadcast: str
    home_record: str
    away_record: str
    home_home_record: str
    away_road_record: str
    home_score: int | None
    away_score: int | None
    book: dict[str, Any] = field(default_factory=dict)


def _state(status: dict[str, Any]) -> str:
    st = ((status or {}).get("type") or {}).get("state")
    return {"pre": "scheduled", "in": "live", "post": "final"}.get(st, "scheduled")


def _record(comp: dict[str, Any], kind: str) -> str:
    for r in comp.get("records") or []:
        if r.get("type") == kind or r.get("name") == kind:
            return r.get("summary", "")
    return ""


def _num(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_scoreboard(data: dict[str, Any]) -> list[Fixture]:
    out: list[Fixture] = []
    for ev in data.get("events", []):
        comp = (ev.get("competitions") or [{}])[0]
        sides = {c.get("homeAway"): c for c in comp.get("competitors", [])}
        if set(sides) != {"home", "away"}:
            continue
        home = teams.canon(sides["home"]["team"]["abbreviation"])
        away = teams.canon(sides["away"]["team"]["abbreviation"])
        if home not in teams.BY_ABBR or away not in teams.BY_ABBR:
            continue
        state = _state(comp.get("status") or ev.get("status"))
        book: dict[str, Any] = {}
        for quote in comp.get("odds") or []:
            if str((quote.get("provider") or {}).get("name", "")).lower() != "draftkings":
                continue
            spread = _num(quote.get("spread"))
            book = {"key": "draftkings", "name": "DraftKings", "source": "ESPN scoreboard",
                    # ESPN's spread is the HOME handicap; the expected home margin is its negative.
                    "spread": spread, "margin": None if spread is None else -spread,
                    "total": _num(quote.get("overUnder")),
                    "home_moneyline": _num((quote.get("homeTeamOdds") or {}).get("moneyLine")),
                    "away_moneyline": _num((quote.get("awayTeamOdds") or {}).get("moneyLine")),
                    "details": quote.get("details")}
        venue = comp.get("venue") or {}
        addr = venue.get("address") or {}
        casts = [n for b in comp.get("broadcasts") or [] for n in (b.get("names") or [])]

        def score(side: str) -> int | None:
            if state == "scheduled":
                return None
            try:
                return int(sides[side].get("score"))
            except (TypeError, ValueError):
                return None

        out.append(Fixture(
            event_id=str(ev["id"]), tip_utc=ev.get("date", ""), state=state,
            status_detail=(((comp.get("status") or {}).get("type") or {}).get("shortDetail") or ""),
            home=home, away=away, neutral=bool(comp.get("neutralSite")),
            venue=venue.get("fullName", ""),
            venue_city=", ".join(x for x in (addr.get("city"), addr.get("state") or addr.get("country")) if x),
            broadcast=" / ".join(dict.fromkeys(casts)),
            home_record=_record(sides["home"], "total"), away_record=_record(sides["away"], "total"),
            home_home_record=_record(sides["home"], "home"), away_road_record=_record(sides["away"], "road"),
            home_score=score("home"), away_score=score("away"), book=book,
        ))
    return out


def slate(now: datetime | None = None, *, horizon_days: int = 4) -> tuple[str, list[Fixture]]:
    """(ET date, fixtures) for the next night that still has a game to tip.

    A night stays on the board while any of its games is live; once every game
    is final the board rolls to the next date with fixtures.
    """
    now = now or datetime.now(timezone.utc)
    day = now.astimezone(ET).date()
    for i in range(horizon_days + 1):
        d = day + timedelta(days=i)
        fixtures = parse_scoreboard(espn.scoreboard(d.strftime("%Y%m%d")))
        if not fixtures:
            continue
        if any(f.state != "final" for f in fixtures):
            return d.isoformat(), fixtures
    return day.isoformat(), []


@dataclass
class Injury:
    team: str
    player_id: str
    name: str
    status: str            # Out | Doubtful | Questionable | Day-To-Day | Probable ...
    detail: str
    note: str
    date: str
    return_date: str

    @property
    def unavailable(self) -> bool:
        return self.status.lower() in {"out", "doubtful", "suspension", "suspended"}


def injuries() -> list[Injury]:
    data = espn.injuries()
    out: list[Injury] = []
    for block in data.get("injuries", []):
        tid = str(block.get("id", ""))
        team = teams.BY_ID.get(tid, (None, None))[1]
        if not team:
            continue
        for inj in block.get("injuries", []):
            ath = inj.get("athlete") or {}
            pid = ""
            for link in ath.get("links") or []:
                m = ATHLETE_ID.search(link.get("href", ""))
                if m:
                    pid = m.group(1)
                    break
            det = inj.get("details") or {}
            detail = " ".join(x for x in (det.get("side") if det.get("side") not in (None, "Not Specified") else None,
                                          det.get("type"), det.get("detail")) if x)
            out.append(Injury(team, pid, ath.get("displayName", ""), inj.get("status", ""), detail,
                              inj.get("shortComment", ""), inj.get("date", ""), det.get("returnDate", "")))
    return out


def rosters(abbrs: list[str]) -> dict[str, dict[str, str]]:
    """{team: {player_id: name}} for the current rosters."""
    def one(abbr: str) -> tuple[str, dict[str, str]]:
        data = espn.get(f"{espn.SITE}/teams/{teams.team_id(abbr)}/roster")
        return abbr, {str(a["id"]): a.get("displayName", "") for a in data.get("athletes", [])}

    with ThreadPoolExecutor(max_workers=8) as pool:
        return dict(pool.map(one, abbrs))


CORE = "https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba"
COACH_CACHE = __import__("pathlib").Path(__file__).resolve().parents[2] / "data" / "coaches"


def head_coaches(season: int, max_age_hours: float = 24.0) -> dict[str, dict[str, Any]]:
    """{team: {name, experience}} from ESPN's core API, cached a day per season."""
    import json as _json
    import time as _time
    path = COACH_CACHE / f"{season}.json"
    if path.exists() and _time.time() - path.stat().st_mtime < max_age_hours * 3600:
        return _json.loads(path.read_text(encoding="utf-8"))

    def one(abbr: str) -> tuple[str, dict[str, Any] | None]:
        try:
            lst = espn.get(f"{CORE}/seasons/{season}/teams/{teams.team_id(abbr)}/coaches")
            items = lst.get("items") or []
            if not items:
                return abbr, None
            c = espn.get(str(items[0]["$ref"]).replace("http://", "https://"))
            return abbr, {"name": " ".join(x for x in (c.get("firstName"), c.get("lastName")) if x),
                          "experience": c.get("experience")}
        except espn.SourceError:
            return abbr, None

    with ThreadPoolExecutor(max_workers=8) as pool:
        out = {a: v for a, v in pool.map(one, teams.all_abbrs()) if v}
    if out:
        COACH_CACHE.mkdir(parents=True, exist_ok=True)
        path.write_text(_json.dumps(out, indent=1) + "\n", encoding="utf-8")
    return out
