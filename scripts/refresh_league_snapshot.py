#!/usr/bin/env python3
"""Refresh the stats.nba.com snapshot and publish it.

stats.nba.com answers home networks but not datacenters (GitHub's runners and
Cloudflare both get dropped), so the Synergy play types and Second Spectrum
tracking the matchup profiles read are pulled here, on a schedule, and pushed:

    git pull --rebase  ->  nbastats.refresh(current season)  ->  commit data/nbastats if it changed  ->  push

A push to main redeploys the board. If this machine is off, the site keeps
the last snapshot, and every profile names the date it was fetched.

Registered with Windows Task Scheduler by scripts/install_league_refresh.ps1.
"""
from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nbamodel import export, nbastats  # noqa: E402

LOG = ROOT / "data" / "nbastats" / "refresh.log"


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)


def log(msg: str) -> None:
    line = f"{datetime.now(timezone.utc).replace(microsecond=0).isoformat()} {msg}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def main() -> int:
    pull = git("pull", "--rebase", "--autostash", "origin", "main")
    if pull.returncode:
        log(f"git pull failed: {pull.stderr.strip()[:300]}")
        return 1
    season = export.current_season(datetime.now(timezone.utc))
    got = nbastats.refresh(season, log=log)
    if not got:
        log("nothing refreshed (stats.nba.com unreachable); snapshot unchanged")
        return 0
    git("add", "--", f"data/nbastats/{season}")
    if git("diff", "--cached", "--quiet").returncode == 0:
        log("snapshot already current")
        return 0
    msg = f"data: refresh stats.nba.com snapshot ({len(got)} tables, season {season})"
    commit = git("-c", "user.name=Alphakiller1", "-c", "user.email=chase4sichi@gmail.com", "commit", "-m", msg)
    if commit.returncode:
        log(f"commit failed: {commit.stderr.strip()[:300]}")
        return 1
    push = git("push", "origin", "HEAD:main")
    if push.returncode:
        log(f"push failed: {push.stderr.strip()[:300]}")
        return 1
    log(msg + " - pushed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
