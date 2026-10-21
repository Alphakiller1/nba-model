"""ESPN's public NBA site API: the one source this model reads.

Every endpoint used here is the same unauthenticated JSON the ESPN site serves.
Requests retry with a short backoff because ESPN's edge intermittently refuses
an agent; a request that still fails raises, so a broken pull is never mistaken
for an empty slate.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

SITE = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
AGENTS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "curl/8.5.0",
)


class SourceError(RuntimeError):
    """ESPN did not answer with usable JSON."""


def get(url: str, *, attempts: int = 4, timeout: float = 20.0) -> Any:
    last: Exception | None = None
    for i in range(attempts):
        agent = AGENTS[i % len(AGENTS)]
        req = urllib.request.Request(url, headers={"User-Agent": agent, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ConnectionError) as exc:
            last = exc
            time.sleep(0.6 * (i + 1))
    raise SourceError(f"{url}: {last}")


def scoreboard(date_yyyymmdd: str) -> dict[str, Any]:
    return get(f"{SITE}/scoreboard?dates={date_yyyymmdd}&limit=50")


def summary(event_id: str) -> dict[str, Any]:
    return get(f"{SITE}/summary?event={event_id}")


def team_schedule(team_id: str, season: int) -> dict[str, Any]:
    """`season` is ESPN's year: the calendar year the season ends (2026-27 is 2027)."""
    return get(f"{SITE}/teams/{team_id}/schedule?season={season}&seasontype=2")


def injuries() -> dict[str, Any]:
    return get(f"{SITE}/injuries")
