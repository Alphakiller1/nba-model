"""Dated, public Databallr evidence. Competition filters are verified after collection.

The provider's advanced endpoint can return playoff rows for a regular-season
request. Raw rows are retained; regular_players() explicitly filters them.
Provider impact estimates are available to model research, never to Research's
factual export. A snapshot collected today is not eligible in an earlier backtest.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import teams

DATA = Path(__file__).resolve().parents[2] / "data" / "databallr"
API = "https://api.databallr.com"
ZONE_FIELDS = (
    ("rim", "At rim", "AtRim"),
    ("short_mid", "Short mid-range", "ShortMidRange"),
    ("long_mid", "Long mid-range", "LongMidRange"),
    ("corner3", "Corner 3", "Corner3"),
    ("arc3", "Above-break 3", "Arc3"),
)


def _request(path: str, params: dict) -> tuple[bytes, str]:
    url = API + path + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"User-Agent": "ChaseAnalytics/1.0", "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=90) as response:
        return response.read(), url


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _root(season: int) -> Path:
    root = DATA / str(season)
    pointer = root / "latest.json"
    if pointer.is_file():
        name = _read(pointer)["snapshot"]
        if not re.fullmatch(r"\d{8}T\d{6}Z", name):
            raise ValueError("Invalid Databallr snapshot identifier")
        return root / "snapshots" / name
    return root


def snapshot(season: int) -> dict:
    root = _root(season)
    if not (root / "manifest.json").is_file():
        return {}
    manifest = _read(root / "manifest.json")
    if manifest.get("season") != season:
        raise ValueError("Databallr snapshot season mismatch")
    for receipt in manifest.get("receipts", []):
        name = receipt.get("file", "")
        if not re.fullmatch(r"(?:players-\d+|advanced-\d+|teams)\.json", name):
            raise ValueError("Invalid Databallr receipt filename")
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != receipt.get("sha256"):
            raise ValueError("Databallr receipt checksum mismatch")
    out = {"manifest": manifest}
    for kind in ("players", "advanced"):
        files = sorted(root.glob(f"{kind}-*.json"), key=lambda p: int(p.stem.rsplit("-", 1)[1]))
        rows = [row for file in files for row in _read(file)]
        if any(int(row.get("year", -1)) != season for row in rows):
            raise ValueError("Databallr returned mixed seasons")
        out[kind] = rows
    out["teams"] = _read(root / "teams.json")
    for side in ("team", "opponent"):
        rows = out["teams"][side]["team_data"]
        codes = {teams.canon(row.get("TeamAbbreviation", "")) for row in rows}
        if codes != set(teams.all_abbrs()) or len(rows) != 30:
            raise ValueError("Incomplete Databallr team cohort")
    return out


def regular_players(season: int, *, advanced: bool = False, as_of: datetime | None = None) -> list[dict]:
    data = snapshot(season)
    if not data:
        return []
    retrieved = datetime.fromisoformat(data["manifest"]["retrieved_at_utc"])
    if as_of is not None and as_of < retrieved:
        return []
    rows = data["advanced" if advanced else "players"]
    selected = [row for row in rows if int(row.get("playoffs") or 0) == 0]
    keys = [(str(row["nba_id"]), str(row.get("TeamId"))) for row in selected]
    if len(keys) != len(set(keys)):
        raise ValueError("Ambiguous player/team rows in Databallr snapshot")
    return selected


def refresh(season: int, *, force: bool = False) -> dict:
    """Validate a complete snapshot before atomically changing its pointer."""
    old = snapshot(season)
    now = datetime.now(timezone.utc)
    if old and not force:
        stamp = datetime.fromisoformat(old["manifest"]["retrieved_at_utc"])
        if (now - stamp).total_seconds() < 6 * 3600:
            return summary(season)
    payloads: dict[str, bytes] = {}
    receipts = []
    for kind, path in (("players", "/v1/nba/players/stats"), ("advanced", "/v1/nba/players/stats/advanced")):
        offset = 0
        all_rows = []
        while True:
            raw, url = _request(path, {"year": season, "playoffs": "false", "limit": 500, "offset": offset})
            rows = json.loads(raw)
            if not isinstance(rows, list) or any(int(row.get("year", -1)) != season for row in rows):
                raise ValueError("Malformed Databallr player response")
            name = f"{kind}-{offset}.json"
            payloads[name] = raw
            receipts.append({"file": name, "url": url, "rows": len(rows), "sha256": hashlib.sha256(raw).hexdigest()})
            all_rows.extend(rows)
            if len(rows) < 500:
                break
            offset += len(rows)
            if offset > 5000:
                raise ValueError("Unexpected Databallr pagination")
        keys = [(row["nba_id"], row.get("TeamId"), int(row.get("playoffs") or 0)) for row in all_rows]
        if not keys or len(set(keys)) != len(keys):
            raise ValueError("Incomplete or duplicated Databallr response")
        if old and len(all_rows) < .9 * len(old[kind]):
            raise ValueError("Databallr coverage regressed; last snapshot retained")
    raw, url = _request("/v1/nba/teams/stats", {"season": season, "leverage": "all"})
    team_data = json.loads(raw)
    if team_data.get("season") != season:
        raise ValueError("Databallr team season mismatch")
    for side in ("team", "opponent"):
        rows = team_data[side]["team_data"]
        if len(rows) != 30 or {teams.canon(r.get("TeamAbbreviation", "")) for r in rows} != set(teams.all_abbrs()):
            raise ValueError("Incomplete Databallr team response")
        for row in rows:
            for _, _, stem in ZONE_FIELDS:
                attempts, made = row.get(stem + "FGA"), row.get(stem + "FGM")
                if not isinstance(attempts, (float, int)) or not isinstance(made, (float, int)):
                    raise ValueError("Missing Databallr zone counts")
                if not math.isfinite(attempts + made) or not 0 <= made <= attempts:
                    raise ValueError("Invalid Databallr zone counts")
    payloads["teams.json"] = raw
    receipts.append({"file": "teams.json", "url": url, "sha256": hashlib.sha256(raw).hexdigest()})
    name = now.strftime("%Y%m%dT%H%M%SZ")
    root = DATA / str(season) / "snapshots" / name
    root.mkdir(parents=True, exist_ok=False)
    for file, payload in payloads.items():
        (root / file).write_bytes(payload)
    manifest = {"source": "Databallr public stats API", "season": season,
                "retrieved_at_utc": now.isoformat(), "requested_filters": {"playoffs": False, "leverage": "all"},
                "competition_policy": "Raw advanced rows retained; regular-season evidence filters playoffs=0.",
                "receipts": receipts}
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    pointer = DATA / str(season) / "latest.json"
    temp = pointer.with_suffix(".tmp")
    temp.write_text(json.dumps({"snapshot": name}), encoding="utf-8")
    temp.replace(pointer)
    return summary(season)


def summary(season: int) -> dict:
    data = snapshot(season)
    if not data:
        return {"source": "Databallr", "season": season, "state": "unavailable"}
    return {"source": "Databallr", "season": season, "state": "available",
            "retrieved_at_utc": data["manifest"]["retrieved_at_utc"],
            "source_updated_at": data["teams"]["team"]["updated_at"],
            "player_rows": len(regular_players(season)), "advanced_rows": len(regular_players(season, advanced=True)),
            "team_rows": 30, "opponent_rows": 30,
            "observation_cutoff": "Provider does not expose the last included game date in this response."}


def team_contexts(season: int) -> dict[str, dict]:
    """Only observed counts/rates; preserve the provider's own zone definitions."""
    from .profiles import ranked

    data = snapshot(season)
    if not data:
        return {}
    out: dict[str, dict] = {}
    for source_side, side in (("team", "offense"), ("opponent", "defense")):
        block = data["teams"][source_side]
        grades = {}
        for code, label, stem in ZONE_FIELDS:
            values = {teams.canon(r["TeamAbbreviation"]): r[stem + "FGM"] / r[stem + "FGA"]
                      for r in block["team_data"] if r[stem + "FGA"] >= 20}
            grades[code] = ranked(values, label + " FG%", "high" if side == "offense" else "low", "pct")
        for row in block["team_data"]:
            team = teams.canon(row["TeamAbbreviation"])
            total = sum(float(row[stem + "FGA"]) for _, _, stem in ZONE_FIELDS)
            zones = []
            for code, label, stem in ZONE_FIELDS:
                attempts, made = int(row[stem + "FGA"]), int(row[stem + "FGM"])
                zones.append({"id": code, "label": label, "attempts": attempts, "made": made,
                              "fg_pct": made / attempts if attempts else None,
                              "fg_grade": grades[code].get(team),
                              "shot_share": attempts / total if total else None})
            out.setdefault(team, {"source": "Databallr", "season": season, "schema": "databallr-pbp-zones-v1",
                                  "leverage_filter": "all", "source_url": "https://databallr.com/stats"})[side] = {
                "source_updated_at": block["updated_at"], "zones": zones}
    return out
