"""How often a player listed on the official injury report actually played.

For every game date in the seasons asked for, read the official report
published about an hour before the date's first tip (official_report.py),
then look each listed player up in that team's box score for the game: he
played if he logged minutes. Rates by Current Status, and by status and
reason (injury, rest, G League, personal).

A report row is counted only when its player can be identified on that team
this season (his name appears in the club's box scores); a two-way player who
never appeared cannot be told apart from a misspelling, and is left out.

    python scripts/designation_rates.py 2025 2026 2027

Writes data/designation_rates.json; parsed reports are cached in
data/official_reports/<season>.json so a rerun reads only new dates.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nbamodel import ingest  # noqa: E402
from nbamodel import official_report as report  # noqa: E402

CACHE = ROOT / "data" / "official_reports"
OUT = ROOT / "data" / "designation_rates.json"
LEAD = timedelta(minutes=60)


PAUSE = 1.5   # seconds between requests: the host blocks a client that asks too fast


def report_for(first_tip_et: datetime) -> tuple[str | None, list[dict]]:
    """The report about LEAD before the first tip: one request (the
    15-minute slot used since 2025-26), and the hourly name used before it."""
    import time
    at = first_tip_et - LEAD
    day = at.strftime("%Y-%m-%d")
    for slot in (report.slot(at), at.strftime("%I%p")):
        url = report.REPORT.format(day=day, slot=slot)
        body = report.fetch(url)
        time.sleep(PAUSE)
        if body:
                try:
                    rows = report.parse(body)
                except Exception:
                    return url, []
                return url, [{"team": r.team, "name": r.name, "status": r.status, "reason": r.reason,
                              "category": r.category} for r in rows]
    return None, []


def season_rates(season: int) -> list[dict]:
    games = ingest.load_games(season)
    lines = ingest.load_players(season)
    by_day: dict[str, list] = defaultdict(list)
    for g in games:
        tip = datetime.fromisoformat(g.date_utc.replace("Z", "+00:00")).astimezone(report.ET)
        by_day[tip.strftime("%Y-%m-%d")].append((tip, g))
    cache_path = CACHE / f"{season}.json"
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    todo = [d for d in sorted(by_day) if d not in cache]

    def one(day: str):
        first = min(t for t, _ in by_day[day])
        return day, report_for(first)

    for day, (url, rows) in map(one, todo):
            cache[day] = {"url": url, "rows": rows}
            print(f"  {season} {day}: {len(rows)} rows" + ("" if url else " (no report)"), flush=True)
    CACHE.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cache, separators=(",", ":")) + "\n", encoding="utf-8")

    known: dict[str, set[str]] = defaultdict(set)          # team -> name keys seen in its box
    minutes: dict[tuple[str, str], dict[str, float]] = defaultdict(dict)  # (event, team) -> name key -> minutes
    for ln in lines:
        key = report.name_key(ln.get("name", ""))
        known[ln["team"]].add(key)
        minutes[(ln["event_id"], ln["team"])][key] = float(ln.get("minutes") or 0)
    out = []
    for day, entry in cache.items():
        events = {}
        for _tip, g in by_day.get(day, []):
            events[g.home] = g.event_id
            events[g.away] = g.event_id
        for row in entry["rows"]:
            eid = events.get(row["team"])
            key = report.name_key(row["name"])
            if not eid or key not in known[row["team"]]:
                continue
            played = minutes.get((eid, row["team"]), {}).get(key, 0.0) > 0
            out.append({**row, "season": season, "day": day, "played": played})
    return out


def summarise(rows: list[dict]) -> dict:
    def tally(key):
        acc: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        for r in rows:
            k = key(r)
            acc[k][0] += r["played"]
            acc[k][1] += 1
        return {k: {"played": round(p / n, 4), "n": n} for k, (p, n) in sorted(acc.items())}
    return {"by_status": tally(lambda r: r["status"]),
            "by_status_reason": tally(lambda r: f"{r['status']}|{r['category']}")}


def main(seasons: list[int]) -> None:
    rows = []
    for season in seasons:
        rows += season_rates(season)
    result = summarise(rows)
    result.update({
        "seasons": seasons, "rows": len(rows),
        "method": "Official NBA injury report about an hour before each date's first tip, "
                  "joined to the team's box score: played = logged minutes.",
    })
    OUT.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["by_status"], indent=2))


if __name__ == "__main__":
    main([int(a) for a in sys.argv[1:]] or [2026])
