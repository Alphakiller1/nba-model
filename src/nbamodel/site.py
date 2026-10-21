"""The model's own GitHub Pages dashboard (alphakiller1.github.io/nba-model).

The authority gate comes before any number: a research-only board that shows
prices first and permissions in a footnote misrepresents itself.
"""
from __future__ import annotations

from html import escape
from typing import Any

from . import teams

CSS = """
:root{--bg:#08090F;--card:#12141D;--line:#232634;--text:#F2F3F7;--text-2:#A4A8B6;--brand:#9A6BFF;
--pos:#3FCF8E;--neg:#FF6B7A}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);
font-family:'DM Sans',system-ui,sans-serif;font-size:15px;line-height:1.45}
main{max-width:1180px;margin:0 auto;padding:24px 16px 64px}
h1,h2{font-family:'Roboto Condensed',sans-serif;letter-spacing:.02em;margin:0 0 8px}
h1{font-size:30px}h2{font-size:21px;margin-top:32px}p{color:var(--text-2);margin:4px 0 12px}
.gate{border:1px solid var(--brand);border-radius:12px;padding:14px 16px;background:#151026}
.gate b{color:var(--brand)}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:12px}
.bd-card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px}
.bd-teams{display:flex;justify-content:space-between;align-items:center;gap:8px;font-weight:700}
.bd-teams img{width:30px;height:30px;vertical-align:middle;margin-right:6px}
.bd-row{display:flex;justify-content:space-between;color:var(--text-2);font-size:13px;margin-top:6px}
.bd-row b{color:var(--text)}.bd-status{font-size:12px;color:var(--text-2)}
.scroll{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:13px}
th,td{padding:6px 8px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
th:first-child,td:first-child{text-align:left}th{color:var(--text-2);font-weight:600}
.pos{color:var(--pos)}.neg{color:var(--neg)}footer{color:var(--text-2);font-size:12px;margin-top:40px}
a{color:var(--brand)}
"""


def _f(v: Any, n: int = 1, signed: bool = False) -> str:
    if v is None:
        return "—"
    s = f"{float(v):+.{n}f}" if signed else f"{float(v):.{n}f}"
    return s


def _card(g: dict[str, Any]) -> str:
    def side(t: str) -> str:
        return f'<span><img src="{escape(teams.logo(t))}" alt="" loading="lazy">{escape(t)}</span>'
    wp = g.get("win_probability")
    rows = [
        ("Model margin (home)", _f(g.get("model_margin"), 1, True)),
        ("DraftKings margin", _f(g.get("market_margin"), 1, True)),
        ("Gap (not an edge)", _f(g.get("market_gap"), 1, True)),
        ("Projected score", f"{_f(g.get('projected_away_score'))} – {_f(g.get('projected_home_score'))}"),
        ("Projected total / DK", f"{_f(g.get('projected_total'))} / {_f(g.get('market_total'))}"),
        ("Home win probability", "—" if wp is None else f"{wp:.0%}"),
        ("Availability (pts)", _f(g.get("availability_margin"), 1, True)),
    ]
    rest = [t for t, b in ((g["away"], g.get("away_b2b")), (g["home"], g.get("home_b2b"))) if b]
    return (f'<article class="bd-card"><div class="bd-teams">{side(g["away"])}<span>@</span>{side(g["home"])}</div>'
            f'<div class="bd-status">{escape(g.get("game_state", ""))} · {escape(g.get("kickoff_utc", ""))}'
            f'{" · back-to-back: " + escape(", ".join(rest)) if rest else ""}</div>'
            + "".join(f'<div class="bd-row"><span>{escape(k)}</span><b>{escape(v)}</b></div>' for k, v in rows)
            + '<div class="bd-row"><span>Status</span><b class="bd-status is-neutral">MONITOR</b></div></article>')


def _live(live: dict[str, Any]) -> str:
    if not live.get("graded"):
        return ""
    return f", margin MAE {live.get('margin_mae')}, Brier {live.get('brier')}"


def render(board: dict[str, Any], ledger_payload: dict[str, Any]) -> str:
    rec = board.get("backtest") or {}
    live = ledger_payload.get("summary") or {}
    gates = "".join(f"<li>{escape(g)}</li>" for g in board.get("unmet_gates", []))
    cards = "".join(_card(g) for g in board.get("games", [])) or "<p>No games on this slate.</p>"
    team_rows = "".join(
        f'<tr><td>{escape(t["name"])}</td><td class="{"pos" if t["net"] > 0 else "neg"}">{t["net"]:+.1f}</td>'
        f'<td>{t["off"]:+.1f}</td><td>{t["def"]:+.1f}</td><td>{t["pace"]:.1f}</td>'
        f'<td>{t["prior_net"]:+.1f}</td><td>{t.get("roster_shift", 0):+.1f}</td><td>{t["games"]}</td></tr>'
        for t in board.get("teams", []))
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>NBA Model — Chase Analytics</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;600;700&family=Roboto+Condensed:wght@700&display=swap" rel="stylesheet">
<style>{CSS}</style></head>
<body><main>
<section id="authority" class="gate"><h1>NBA Model</h1>
<p><b>{escape(board.get("authority", ""))}</b> · may_bet: false · not betting advice.
{escape(board.get("note", ""))}</p>
<p>{escape(board.get("evidence", ""))}</p><p>Unmet promotion gates:</p><ul>{gates}</ul></section>
<section id="board"><h2>Slate · {escape(board.get("slate_date", ""))}</h2>
<p>Generated {escape(board.get("generated_at_utc", ""))}. Lines: DraftKings via ESPN.</p>
<div class="grid">{cards}</div></section>
<section id="ratings"><h2>Team ratings</h2><p>Points per 100 possessions above league average; defense is points
allowed (lower is better). Prior is last season regressed {board["constants"].get("carry")}, shifted for roster turnover.</p>
<div class="scroll"><table><thead><tr><th>Team</th><th>Net</th><th>Off</th><th>Def</th><th>Pace</th><th>Prior net</th>
<th>Roster shift</th><th>GP</th></tr></thead><tbody>{team_rows}</tbody></table></div></section>
<section id="record"><h2>Record</h2>
<p>2025-26 walk-forward holdout: margin MAE {rec.get("model_margin_mae_priced")} vs DraftKings close
{rec.get("market_margin_mae")} ({rec.get("priced_games")} priced games); total MAE {rec.get("model_total_mae_priced")}
vs {rec.get("market_total_mae")}; Brier {rec.get("brier")}.</p>
<p>Live this season: {live.get("graded", 0)} graded games{_live(live)}.</p></section>
<section id="methodology"><h2>Methodology</h2>
<p>Weighted ridge regressions on ESPN box scores: each club's points per 100 possessions scored and allowed, and
possessions per game, refit before every slate with a {board["constants"].get("half_life_days"):.0f}-day half-life
and pulled toward a prior from last season. League-wide home-court and back-to-back terms are fitted on 2023-24 and
2024-25. Tonight's rotation adjusts each club for players listed Out or Doubtful on ESPN's injury report. See
<a href="https://github.com/Alphakiller1/nba-model">the repository</a> for the backtest.</p></section>
<footer>Chase Analytics · research only ·
If you or someone you know has a gambling problem, call 1-800-GAMBLER.</footer>
</main></body></html>
"""
