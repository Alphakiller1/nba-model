"""One pass over every final game of a season: box score, player lines, stints.

    python -m nbamodel.bulk 2020 2021 ... (ESPN season years)

Games and player lines extend ``data/games``; stints go to
``data/stints/{season}.csv.gz``. Both are resumable: a game already present in
a season's stint file is not fetched again.
"""
from __future__ import annotations

import csv
import gzip
import io
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import fields
from pathlib import Path

from . import espn, ingest, stints

STINTS = Path(__file__).resolve().parents[2] / "data" / "stints"
STINT_FIELDS = [f.name for f in fields(stints.Stint)]


def stint_path(season: int) -> Path:
    return STINTS / f"{season}.csv.gz"


def load_stints(season: int) -> list[dict[str, str]]:
    path = stint_path(season)
    if not path.exists():
        return []
    with gzip.open(path, "rt", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _write_stints(season: int, rows: list[dict]) -> None:
    STINTS.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=STINT_FIELDS, lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    tmp = stint_path(season).with_suffix(".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8", newline="") as fh:
        fh.write(buf.getvalue())
    tmp.replace(stint_path(season))


def _row(s: stints.Stint) -> dict:
    d = {k: getattr(s, k) for k in STINT_FIELDS}
    d["home_players"] = "|".join(s.home_players)
    d["away_players"] = "|".join(s.away_players)
    d["seconds"] = round(s.seconds, 1)
    return d


def run(season: int, workers: int = 10, log=print) -> None:
    games = {r["event_id"]: r for r in ingest._read(ingest._path(season, "games"))}
    players = ingest._read(ingest._path(season, "players"))
    have_players = {r["event_id"] for r in players}
    st_rows = load_stints(season)
    have_stints = {r["event_id"] for r in st_rows}
    events = [e["id"] for e in ingest.season_events(season) if e["completed"]]
    # A game is complete only with its box score AND its stints; an interrupted
    # run can leave stints written for games whose box rows were not.
    todo = [e for e in events if e not in have_stints or e not in games]
    st_rows = [r for r in st_rows if r["event_id"] in games]
    log(f"{season}: {len(events)} finals, {len(have_stints)} with stints, {len(todo)} to fetch")

    def one(eid):
        try:
            data = espn.summary(eid)
        except espn.SourceError as exc:
            return eid, None, str(exc)
        return eid, data, None

    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for eid, data, err in pool.map(one, todo):
            if data is None:
                log(f"  skip {eid}: {err}")
                continue
            parsed = ingest.parse_summary(eid, season, data)
            if parsed is None:
                continue
            game, lines = parsed
            if eid not in games:
                games[eid] = {k: getattr(game, k) for k in ingest.GAME_FIELDS}
            if eid not in have_players:
                players.extend({k: getattr(p, k) for k in ingest.PLAYER_FIELDS} for p in lines)
            st_rows.extend(_row(s) for s in stints.parse(eid, data))
            done += 1
            if done % 200 == 0:
                log(f"  {season}: {done}/{len(todo)}")
                _write_stints(season, st_rows)
    ingest._write(ingest._path(season, "games"), ingest.GAME_FIELDS,
                  sorted(games.values(), key=lambda r: (r["date_utc"], r["event_id"])))
    ingest._write(ingest._path(season, "players"), ingest.PLAYER_FIELDS, players)
    _write_stints(season, st_rows)
    log(f"{season}: done, {len(games)} games, {len(st_rows)} stints")


if __name__ == "__main__":
    for s in sys.argv[1:]:
        run(int(s), log=lambda m: print(m, flush=True))
