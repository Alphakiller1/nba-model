"""Basketball-Reference season advanced tables: BPM, OBPM, DBPM, VORP, usage.

One page per season (``leagues/NBA_{year}_advanced.html``), fetched politely
and cached in ``data/bbref``. Box Plus/Minus is the box-score prior for the
player ratings: a player with little lineup data is read mostly through it.

Players are matched to ESPN athlete ids by normalized name within a season;
a traded player's multi-team total row (2TM, 3TM) is the one kept.
"""
from __future__ import annotations

import csv
import re
import time
import unicodedata
import urllib.request
from html import unescape
from pathlib import Path

DATA = Path(__file__).resolve().parents[2] / "data" / "bbref"
URL = "https://www.basketball-reference.com/leagues/NBA_{year}_advanced.html"
FIELDS = ["name", "team", "pos", "age", "games", "mp", "per", "ts_pct", "usg_pct", "obpm", "dbpm", "bpm",
          "vorp", "ws", "orb_pct", "drb_pct", "ast_pct", "stl_pct", "blk_pct", "tov_pct"]
ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
# Stat names carry digits (pct_fga_00_03, fg3a) and spacer columns are named
# DUMMY; a narrower pattern silently dropped every such column.
CELL = re.compile(r'<t[dh][^>]*data-stat="([A-Za-z0-9_]+)"[^>]*>(.*?)</t[dh]>', re.S)
TAG = re.compile(r"<[^>]+>")
SUFFIX = re.compile(r"\b(jr|sr|ii|iii|iv|v)\b\.?")


def norm(name: str) -> str:
    s = unicodedata.normalize("NFKD", unescape(name or "")).encode("ascii", "ignore").decode().lower()
    s = SUFFIX.sub("", s.replace(".", " "))
    return re.sub(r"[^a-z]", "", s)


def parse(html: str) -> list[dict[str, str]]:
    start = html.find('id="advanced"')
    body = html[start:] if start >= 0 else html
    seen: set[str] = set()
    out = []
    for raw in ROW.findall(body):
        cells = {k: TAG.sub("", v).strip() for k, v in CELL.findall(raw)}
        name = cells.get("name_display")
        if not name or name == "Player" or not cells.get("mp"):
            continue
        key = norm(name)
        if key in seen:  # team-split rows follow the multi-team total
            continue
        seen.add(key)
        out.append({"name": unescape(name), "team": cells.get("team_name_abbr", ""),
                    **{f: cells.get(f, "") for f in FIELDS[2:]}})
    return out


def path(year: int) -> Path:
    return DATA / f"advanced_{year}.csv"


def fetch(year: int, *, force: bool = False) -> Path:
    p = path(year)
    if p.exists() and not force:
        return p
    req = urllib.request.Request(URL.format(year=year), headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        rows = parse(resp.read().decode("utf-8", "replace"))
    if len(rows) < 300 and year < 2027:
        raise RuntimeError(f"bbref {year}: only {len(rows)} players parsed")
    DATA.mkdir(parents=True, exist_ok=True)
    with p.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    time.sleep(4)  # Sports-Reference asks for no more than 20 requests a minute
    return p


def load(year: int) -> dict[str, dict[str, str]]:
    """normalized name -> row."""
    p = path(year)
    if not p.exists():
        return {}
    with p.open(newline="", encoding="utf-8") as fh:
        return {norm(r["name"]): r for r in csv.DictReader(fh)}


# -- team-season tables and head coaches --------------------------------------
# One page per season each; Basketball-Reference serves several tables inside
# HTML comments, which are parsed the same way.

TEAM_URL = "https://www.basketball-reference.com/leagues/NBA_{year}.html"
COACH_URL = "https://www.basketball-reference.com/leagues/NBA_{year}_coaches.html"
TEAM_TABLES = ("advanced-team", "shooting-team", "shooting-opponent")
FULL_NAME_ALIASES = {"los angeles clippers": "LAC", "la clippers": "LAC", "charlotte bobcats": "CHA",
                     "new orleans hornets": "NO"}


def _get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        html = resp.read().decode("utf-8", "replace")
    time.sleep(4)
    return html.replace("<!--", "").replace("-->", "")


def _table_rows(html: str, table_id: str) -> list[dict[str, str]]:
    i = html.find(f'id="{table_id}"')
    if i < 0:
        return []
    body = html[i:html.find("</table>", i)]
    rows = []
    for raw in ROW.findall(body):
        cells = {k: TAG.sub("", v).strip() for k, v in CELL.findall(raw)}
        if cells.get("team") and cells["team"] not in ("Team", "League Average"):
            rows.append(cells)
    return rows


def team_path(year: int) -> Path:
    return DATA / f"teams_{year}.json"


def coach_path(year: int) -> Path:
    return DATA / f"coaches_{year}.json"


def fetch_team_seasons(year: int, *, force: bool = False) -> Path:
    import json
    p = team_path(year)
    if p.exists() and not force:
        return p
    html = _get(TEAM_URL.format(year=year))
    out = {tid: _table_rows(html, tid) for tid in TEAM_TABLES}
    if len(out["advanced-team"]) < 30 and year < 2027:
        raise RuntimeError(f"bbref teams {year}: {len(out['advanced-team'])} rows")
    DATA.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    return p


def fetch_coaches(year: int, *, force: bool = False) -> Path:
    import json
    p = coach_path(year)
    if p.exists() and not force:
        return p
    html = _get(COACH_URL.format(year=year))
    i = html.find('id="NBA_coaches"')
    body = html[i:html.find("</table>", i)] if i >= 0 else html
    rows = []
    for raw in ROW.findall(body):
        cells = {k: TAG.sub("", v).strip() for k, v in CELL.findall(raw)}
        if cells.get("coach") and cells.get("team") and cells["coach"] != "Coach":
            rows.append({k: cells.get(k, "") for k in ("coach", "team", "seas_num_franch", "seas_num_overall",
                                                       "cur_g", "cur_w", "cur_l", "fr_g", "fr_w", "fr_l",
                                                       "car_g", "car_w", "car_l")})
    if len(rows) < 30:
        raise RuntimeError(f"bbref coaches {year}: {len(rows)} rows")
    DATA.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rows, indent=1) + "\n", encoding="utf-8")
    return p


def load_team_seasons(year: int) -> dict:
    import json
    p = team_path(year)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def load_coaches(year: int) -> list[dict]:
    import json
    p = coach_path(year)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
