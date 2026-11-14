"""The league's official injury report: every game designation, exactly.

ESPN's injury feed folds Questionable, Doubtful and Probable into
"Day-To-Day". The NBA publishes the official report as a PDF every fifteen
minutes on game days (official.nba.com/nba-injury-report-*), listing each
player's Current Status - Out, Doubtful, Questionable, Probable, Available -
and the reason (Injury/Illness, G League assignment, rest, personal).

The PDF is a table. Text is read with its page coordinates and placed into
columns by the header row's x positions. A matchup and a team are printed
once, on the first row of their block, and carry down; a long reason wraps
onto lines just above and below its player's row and is attached to the
nearest player row.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable
from zoneinfo import ZoneInfo

from . import teams

ET = ZoneInfo("America/New_York")
REPORT = "https://ak-static.cms.nba.com/referee/injury/Injury-Report_{day}_{slot}.pdf"
STATUSES = ("Out", "Doubtful", "Questionable", "Probable", "Available")
HEADS = ("Matchup", "Team", "Player", "Current", "Reason")
SUFFIX = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv", "v"}


@dataclass(frozen=True)
class Row:
    team: str           # club abbreviation
    matchup: str        # "TOR@PHI"
    name: str           # "Scottie Barnes" (the report prints "Barnes, Scottie")
    status: str         # one of STATUSES
    reason: str         # "Injury/Illness - Left Groin Strain"

    @property
    def category(self) -> str:
        """injury | g-league | rest | personal | suspension | other"""
        r = self.reason.lower()
        if r.startswith("injury"):
            return "injury"
        if r.startswith("g league"):
            return "g-league"
        if "rest" in r:
            return "rest"
        if "personal" in r:
            return "personal"
        if "suspension" in r:
            return "suspension"
        return "other"


def slot(at: datetime) -> str:
    """The report slot at or before ``at`` (ET), e.g. "04_15PM"."""
    at = at.astimezone(ET)
    minute = at.minute - at.minute % 15
    return at.replace(minute=minute).strftime("%I_%M%p")


def url_for(at: datetime) -> str:
    at = at.astimezone(ET)
    return REPORT.format(day=at.strftime("%Y-%m-%d"), slot=slot(at))


def fetch(url: str, timeout: float = 20.0) -> bytes | None:
    """The PDF at ``url``, or None when that slot is not published (403).

    Read with the requests library under its own name: the host refuses
    Python's built-in urllib client outright.
    """
    import requests
    try:
        response = requests.get(url, timeout=timeout)
    except requests.RequestException:
        return None
    body = response.content
    return body if response.status_code == 200 and body[:4] == b"%PDF" else None


def latest(now: datetime | None = None, *, slots: int = 8, pause: float = 0.5) -> tuple[str | None, list[Row]]:
    """(url, rows) of the newest report published today.

    Reports land every fifteen minutes on game days, so the newest is within
    the last few slots; at most ``slots`` are tried, ``pause`` seconds apart.
    The host blocks a client that requests too fast, so this never sweeps.
    """
    import time
    now = (now or datetime.now(ET)).astimezone(ET)
    for back in range(slots):
        at = now - timedelta(minutes=15 * back)
        if at.date() != now.date():
            break
        url = url_for(at)
        body = fetch(url)
        if body:
            return url, parse(body)
        time.sleep(pause)
    return None, []


def _team_abbr(name: str) -> str | None:
    key = re.sub(r"[^a-z0-9]", "", name.lower())
    for abbr in teams.all_abbrs():
        full = re.sub(r"[^a-z0-9]", "", teams.name(abbr).lower())
        if key == full or (key == "laclippers" and abbr == "LAC"):
            return abbr
    return None


def display_name(report_name: str) -> str:
    """"Johnson Jr., Morez" -> "Morez Johnson Jr."; "de Larrea, Sergio" -> "Sergio de Larrea"."""
    last, _, first = report_name.partition(",")
    if not first:
        return report_name.strip()
    last_parts = last.split()
    suffix = [w for w in last_parts if w.lower() in SUFFIX]
    core = [w for w in last_parts if w.lower() not in SUFFIX]
    return " ".join([first.strip()] + core + suffix)


def name_key(name: str) -> str:
    parts = re.findall(r"[a-z0-9]+", name.lower().replace("'", "").replace(".", ""))
    if len(parts) > 2 and parts[-1] in {"jr", "sr", "ii", "iii", "iv", "v"}:
        parts.pop()
    return "".join(parts)


def _words(body: bytes) -> list[tuple[int, float, float, str]]:
    import pypdf
    reader = pypdf.PdfReader(io.BytesIO(body))
    words: list[tuple[int, float, float, str]] = []
    for page_no, page in enumerate(reader.pages):
        def visit(text, cm, tm, _font, _size, page_no=page_no):
            text = text.strip()
            if text:
                words.append((page_no, round(tm[5] * cm[3] + cm[5], 1), round(tm[4] * cm[0] + cm[4], 1), text))
        page.extract_text(visitor_text=visit)
    return words


def parse(body: bytes) -> list[Row]:
    words = _words(body)
    head = {}
    for _p, _y, x, t in words:
        if t in HEADS and t not in head:
            head[t] = x
    if len(head) < len(HEADS):
        raise ValueError("official injury report layout changed")
    bounds = [("matchup", head["Matchup"] - 2), ("team", head["Team"] - 2), ("player", head["Player"] - 2),
              ("status", head["Current"] - 2), ("reason", head["Reason"] - 2)]

    def column(x: float) -> str | None:
        found = None
        for name, start in bounds:
            if x >= start:
                found = name
        return found

    lines: dict[tuple[int, float], dict[str, list[tuple[float, str]]]] = {}
    for p, y, x, t in words:
        col = column(x)
        if col:
            lines.setdefault((p, y), {}).setdefault(col, []).append((x, t))
    text = {k: {c: " ".join(t for _, t in sorted(v)) for c, v in cols.items()} for k, cols in lines.items()}

    rows: list[dict] = []
    loose: list[tuple[int, float, str]] = []
    matchup = team = ""
    for (p, y) in sorted(text, key=lambda k: (k[0], -k[1])):
        line = text[(p, y)]
        if "Matchup" in line.get("matchup", "") or "Page" in line.get("player", "").split()[:1]:
            continue
        if re.fullmatch(r"[A-Z]{2,4}@[A-Z]{2,4}", line.get("matchup", "")):
            matchup = line["matchup"]
        if line.get("team"):
            team = _team_abbr(line["team"]) or team
        player, status = line.get("player", ""), line.get("status", "")
        if status in STATUSES and player and team:
            rows.append({"p": p, "y": y, "team": team, "matchup": matchup, "name": display_name(player),
                         "status": status, "reason": [line.get("reason", "")]})
        elif line.get("reason") and not player:
            loose.append((p, y, line["reason"]))
    for p, y, reason in loose:
        near = [r for r in rows if r["p"] == p and abs(r["y"] - y) <= 12]
        if near:
            target = min(near, key=lambda r: abs(r["y"] - y))
            target["reason"].append((y, reason))
    out = []
    for r in rows:
        first, *wrapped = r["reason"]
        above = [t for y, t in sorted(wrapped, key=lambda w: -w[0]) if y > r["y"]]
        below = [t for y, t in sorted(wrapped, key=lambda w: -w[0]) if y < r["y"]]
        reason = " ".join(part for part in above + [first] + below if part)
        reason = re.sub(r"(\w)- (\w)", r"\1-\2", " ".join(reason.split()))  # "Two- Way" wrapped at the hyphen
        out.append(Row(r["team"], r["matchup"], r["name"], r["status"], reason))
    return out


def by_player(rows: Iterable[Row], roster: dict[str, dict[str, str]]) -> dict[str, dict[str, Row]]:
    """{team: {player_id: row}} for rows whose name matches one player on the club's roster."""
    out: dict[str, dict[str, Row]] = {}
    for row in rows:
        people = roster.get(row.team) or {}
        hits = [pid for pid, name in people.items() if name_key(name) == name_key(row.name)]
        if len(hits) == 1:
            out.setdefault(row.team, {})[hits[0]] = row
    return out
