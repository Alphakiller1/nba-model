"""Free public CourtSketch detections, retained with their experimental status.

Only /api/tracking public endpoints are used. No research key, account or paid
route. Coverage-linked efficiency is points per FGA, NOT full-possession PPP.
All detections stay outside the public factual exports and unvalidated labels
receive zero score-forecast weight. Named sets can overlap.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from . import teams
from .dynamics import utc

DATA = Path(__file__).resolve().parents[2] / "data" / "tactics" / "courtsketch"
BASE = "https://courtsketch.com/api/tracking"


def get(path: str) -> bytes:
    req = urllib.request.Request(BASE + path, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=50) as response:
        return response.read()


def refresh(season: int, force: bool = False) -> dict:
    DATA.mkdir(parents=True, exist_ok=True)
    manifest_path = DATA / "manifest.json"
    old = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    now = datetime.now(timezone.utc)
    if old and not force and (now - utc(old["retrieved_at_utc"])).total_seconds() < 6 * 3600:
        return {"games": len(old["games"]), "cached": True}
    index = json.loads(get("/games"))
    selected = [
        g for g in index if str(g["id"]).startswith((f"002{str(season - 1)[-2:]}", f"002{str(season - 2)[-2:]}"))
    ]
    if not selected:
        raise ValueError("No requested regular-season tracking games")
    previous = {g["id"]: g for g in old.get("games", [])}

    def collect(game):
        prior = previous.get(game["id"], {})
        path = DATA / prior.get("file", game["id"] + ".json.gz")
        if path.is_file() and prior.get("complete") and game.get("complete") and not force:
            if hashlib.sha256(gzip.decompress(path.read_bytes())).hexdigest() == prior["sha256"]:
                return prior
        raw = get("/game/" + game["id"])
        payload = json.loads(raw)
        if payload["index"]["game_id"] != game["id"] or payload["index"]["date"] != game["date"]:
            raise ValueError("Tracking identity mismatch")
        for layer in ("on_ball", "sets"):
            block = payload.get("actions", {}).get(layer, {})
            if not isinstance(block.get("fields"), list) or not isinstance(block.get("rows"), list):
                raise ValueError("Tracking schema changed")
        digest = hashlib.sha256(raw).hexdigest()
        # Content-addressed writes preserve every file referenced by the last
        # good manifest if another game's refresh fails partway through.
        path = DATA / (game["id"] + "-" + digest[:16] + ".json.gz")
        temp = path.with_suffix(".tmp")
        temp.write_bytes(gzip.compress(raw, mtime=0))
        temp.replace(path)
        return {
            **game,
            "retrieved_at_utc": now.isoformat(),
            "source_url": BASE + "/game/" + game["id"],
            "sha256": digest,
            "file": path.name,
        }

    # A failure never changes the manifest; old immutable game receipts stand.
    with ThreadPoolExecutor(max_workers=3) as pool:
        collected = list(pool.map(collect, selected))
    keep = {g["id"]: g for g in old.get("games", [])}
    keep.update({g["id"]: g for g in collected})
    manifest = {
        "source": "CourtSketch public tracking viewer",
        "retrieved_at_utc": now.isoformat(),
        "source_url": "https://courtsketch.com/tracking",
        "games": list(keep.values()),
        "label_quality": "Experimental provider detections, not possession-tagged ground truth",
        "forecast_weight": 0.0,
    }
    temp = manifest_path.with_suffix(".tmp")
    temp.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    temp.replace(manifest_path)
    return {"games": len(keep), "complete_games": sum(g.get("complete", False) for g in keep.values())}


def rows(block: dict) -> list[dict]:
    return [dict(zip(block["fields"], r, strict=True)) for r in block.get("rows", [])]


def linked_attempts(payload: dict) -> list[dict]:
    """Last preceding same-team tag, without crossing another shot/TO/FT/period.

    This deliberately reports shot outcomes, not inferred possession outcomes.
    A possession can contain several screens/sets, hence event shares aren't a
    partition of possessions and named-set rows are explicitly nonexclusive.
    """
    index, actions = payload["index"], payload.get("actions", {})
    people = index.get("players", {})
    detections = []
    for r in rows(actions.get("on_ball", {"fields": [], "rows": []})):
        player = people.get(str(r.get("handler")), {})
        if r.get("big") and player.get("team"):
            detections.append({"t": r["t"], "team": player["team"], "kind": "coverage", "label": r["big"]})
    for r in rows(actions.get("sets", {"fields": [], "rows": []})):
        detections.append({"t": r["t0"], "team": r["team"], "kind": "formation", "label": r["template"]})
    detections.sort(key=lambda r: r["t"])
    plays = sorted(index["plays"], key=lambda p: (p["t"], p["n"]))
    out, last_terminal, last_period = [], float("-inf"), None
    for play in plays:
        if last_period is not None and play.get("p") != last_period:
            last_terminal = play["t"]  # conservatively discard pre-boundary detections
        last_period = play.get("p")
        if play["type"] not in {"2pt", "3pt", "heave", "turnover", "freethrow", "free throw", "period"}:
            continue
        if play["type"] in {"2pt", "3pt", "heave"} and play.get("made") is not None and play.get("f"):
            candidates = [
                r
                for r in detections
                if r["team"] == play["team"] and max(last_terminal, play["t"] - 12000) < r["t"] <= play["t"]
            ]
            latest = {}
            for r in candidates:
                key = (r["kind"], r["label"] if r["kind"] == "formation" else "last")
                latest[key] = r
            for r in latest.values():
                out.append(
                    {
                        "game_id": index["game_id"],
                        "date": index["date"],
                        "event": play["n"],
                        "offense": teams.canon(play["team"]),
                        "kind": r["kind"],
                        "label": r["label"],
                        "made": int(play["made"]),
                        "value": 2 if play["type"] == "2pt" else 3,
                    }
                )
        last_terminal = play["t"]
    return out


def contexts(season: int, as_of: str) -> dict:
    path = DATA / "manifest.json"
    if not path.exists():
        return {}
    manifest = json.loads(path.read_text(encoding="utf-8"))
    acc = defaultdict(lambda: defaultdict(lambda: {"attempts": 0, "made": 0, "points": 0, "games": set()}))
    known_games = defaultdict(set)
    for game in manifest["games"]:
        game_season = int(game["date"][:4]) + (1 if int(game["date"][5:7]) >= 8 else 0)
        # Experimental detector training cutoffs are unavailable. Receipt-time
        # eligibility prevents retrospective use in earlier forecasts/backtests.
        if game_season != season or not game.get("complete") or utc(game["retrieved_at_utc"]) > utc(as_of):
            continue
        raw = gzip.decompress((DATA / game.get("file", game["id"] + ".json.gz")).read_bytes())
        if hashlib.sha256(raw).hexdigest() != game["sha256"]:
            raise ValueError("Tracking receipt mismatch")
        payload = json.loads(raw)
        off_to_def = {
            teams.canon(game["home"]): teams.canon(game["away"]),
            teams.canon(game["away"]): teams.canon(game["home"]),
        }
        for team in off_to_def:
            known_games[team].add(game["id"])
        for row in linked_attempts(payload):
            for team, side in ((row["offense"], "offense"), (off_to_def[row["offense"]], "defense")):
                a = acc[team][(side, row["kind"], row["label"])]
                a["attempts"] += 1
                a["made"] += row["made"]
                a["points"] += row["made"] * row["value"]
                a["games"].add(row["game_id"])
    out = {}
    for team, values in acc.items():
        records = []
        denominators = defaultdict(int)
        for (side, kind, _), v in values.items():
            denominators[(side, kind)] += v["attempts"]
        for (side, kind, label), v in sorted(values.items()):
            records.append(
                {
                    "side": side,
                    "kind": kind,
                    "label": label,
                    "attempts": v["attempts"],
                    "made": v["made"],
                    "fg_pct": v["made"] / v["attempts"],
                    "points_per_fga": v["points"] / v["attempts"],
                    "games": len(v["games"]),
                    "share_of_linked_tags_within_kind": v["attempts"] / denominators[(side, kind)],
                    "sample_qualified": v["attempts"] >= 30 and len(v["games"]) >= 3,
                }
            )
        out[team] = {
            "source": manifest["source"],
            "season": season,
            "as_of": as_of,
            "tracked_complete_games": len(known_games[team]),
            "rows": records,
            "label_quality": manifest["label_quality"],
            "forecast_weight": 0.0,
            "scope": (
                "Tag-linked field goals within 12 seconds; excludes intervening terminals "
                "and period boundaries. Not PPP or possession frequency."
            ),
            "formation_policy": "Named sets are experimental and nonexclusive.",
        }
    return out
