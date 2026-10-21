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
SHOTS = Path(__file__).resolve().parents[2] / "data" / "shots"
STINT_FIELDS = [f.name for f in fields(stints.Stint)]
SHOT_FIELDS = [f.name for f in fields(stints.Shot)]
# Shots (with the ten on the floor) are kept for these seasons onward; older
# seasons need only stints.
FIRST_SHOT_SEASON = 2026


def stint_path(season: int) -> Path:
    return STINTS / f"{season}.csv.gz"


def load_stints(season: int) -> list[dict[str, str]]:
    path = stint_path(season)
    if not path.exists():
        return []
    with gzip.open(path, "rt", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _write_gz(path: Path, names: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=names, lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    tmp = path.with_suffix(".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8", newline="") as fh:
        fh.write(buf.getvalue())
    tmp.replace(path)


def _write_stints(season: int, rows: list[dict]) -> None:
    _write_gz(stint_path(season), STINT_FIELDS, rows)


def shot_path(season: int) -> Path:
    return SHOTS / f"{season}.csv.gz"


def load_shots(season: int) -> list[dict[str, str]]:
    path = shot_path(season)
    if not path.exists():
        return []
    with gzip.open(path, "rt", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _shot_row(s: stints.Shot) -> dict:
    d = {k: getattr(s, k) for k in SHOT_FIELDS}
    d["offense"] = "|".join(s.offense)
    d["defense"] = "|".join(s.defense)
    d["distance"] = round(s.distance, 1)
    return d


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
    want_shots = season >= FIRST_SHOT_SEASON
    sh_rows = load_shots(season) if want_shots else []
    have_shots = {r["event_id"] for r in sh_rows}
    events = [e["id"] for e in ingest.season_events(season) if e["completed"]]
    # A game is complete only with its box score AND its stints (and, for
    # recent seasons, its shots); an interrupted run can leave one without the
    # other, so the game is fetched again and its partial rows replaced.
    todo = [e for e in events if e not in have_stints or e not in games or (want_shots and e not in have_shots)]
    redo = set(todo)
    st_rows = [r for r in st_rows if r["event_id"] in games and r["event_id"] not in redo]
    sh_rows = [r for r in sh_rows if r["event_id"] not in redo]
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
            st, sh = stints.parse_all(eid, data)
            st_rows.extend(_row(s) for s in st)
            if want_shots:
                sh_rows.extend(_shot_row(s) for s in sh)
            done += 1
            if done % 200 == 0:
                log(f"  {season}: {done}/{len(todo)}")
                _write_stints(season, st_rows)
                if want_shots:
                    _write_gz(shot_path(season), SHOT_FIELDS, sh_rows)
    ingest._write(ingest._path(season, "games"), ingest.GAME_FIELDS,
                  sorted(games.values(), key=lambda r: (r["date_utc"], r["event_id"])))
    ingest._write(ingest._path(season, "players"), ingest.PLAYER_FIELDS, players)
    _write_stints(season, st_rows)
    if want_shots:
        _write_gz(shot_path(season), SHOT_FIELDS, sh_rows)
    log(f"{season}: done, {len(games)} games, {len(st_rows)} stints, {len(sh_rows)} shots")


if __name__ == "__main__":
    for s in sys.argv[1:]:
        run(int(s), log=lambda m: print(m, flush=True))
