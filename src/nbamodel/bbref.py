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
CELL = re.compile(r'<t[dh][^>]*data-stat="([a-z_]+)"[^>]*>(.*?)</t[dh]>', re.S)
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
