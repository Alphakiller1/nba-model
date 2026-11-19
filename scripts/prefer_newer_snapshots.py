#!/usr/bin/env python3
"""Keep the newer copy of each league snapshot after the CI cache restore.

stats.nba.com answers home networks but not GitHub's runners, so the
stats.nba.com tables (data/nbastats) and the NBA coverage capture
(data/coverage) are refreshed on a home machine and committed
(scripts/refresh_league_snapshot.py). The deploy job then restores its cache
over the checkout, and a restore overwrites files: the board was built from
the cached copies, not the committed ones (2026-11-19: tables committed on
11-18 were published as of 11-07).

For every committed file under those directories this compares the fetch
stamp of the committed copy with the copy on disk and puts the committed one
back when it is newer. A file with no readable stamp is left as restored.

    python scripts/prefer_newer_snapshots.py [dir ...]
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIRS = ("data/nbastats", "data/coverage")
STAMPS = ("fetched_at_utc", "retrieved_at_utc")


def stamp(text: str) -> datetime | None:
    """The snapshot's fetch time (timezone-aware), or None."""
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    for key in STAMPS:
        value = data.get(key)
        if isinstance(value, str) and value:
            try:
                when = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return None
            return when if when.tzinfo else when.replace(tzinfo=timezone.utc)
    return None


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True,
                          encoding="utf-8").stdout


def prefer_newer(dirs=DIRS) -> list[str]:
    restored = []
    for rel in git("ls-files", "--", *dirs).splitlines():
        if not rel.endswith(".json"):
            continue
        path = ROOT / rel
        committed = git("show", f"HEAD:{rel}")
        mine = stamp(committed)
        if mine is None:
            continue
        on_disk = stamp(path.read_text(encoding="utf-8")) if path.is_file() else None
        if on_disk is None or mine > on_disk:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(committed, encoding="utf-8")
            restored.append(rel)
    return restored


if __name__ == "__main__":
    put_back = prefer_newer(tuple(sys.argv[1:]) or DIRS)
    print(f"committed snapshot newer than the cache: {len(put_back)} file(s) put back")
    for rel in put_back[:20]:
        print("  " + rel)
