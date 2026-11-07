# NBA pace, trajectory risk and tactical diagnostics

Both ending years 2026 and 2027 now have private team/matchup diagnostics for
season and recent-five shooting reversion, pace uncertainty, disjoint recent
process changes, roster continuity and observed G/F/C personnel efficiency.
All 30 teams retain their season identity. The RAPM/team point forecasts and
RESEARCH_ONLY authority remain unchanged; new diagnostic score weight is zero.

## Evidence and denominators

ESPN game/player boxes supply shot attempts, turnovers, rebounds and minutes.
OT duration is accepted only when both clubs' player minutes reconcile to
240 + 25×OT within three minutes. Personnel comes from complete five-player
stints and that game's listed positions; possession denominators are explicitly
estimated as FGA − ORB + TOV + 0.44×FTA. Unknown lineups are excluded.

Existing free NBA zone/non-zone and play-type sources are retained separately
for both seasons, with receipt eligibility. Play type does not establish
defensive coverage. Official measured coverage and experimental detections
must never be presented as the same evidence class.

The free [CourtSketch public viewer](https://courtsketch.com/tracking) adds
experimental current-season coverage and named-set detections. The initial
index contains 123 games, of which 115 are complete, across all 30 teams.
There are no previous-season optical detections in this public index.
Only public `/api/tracking` endpoints are used; no frames, account, research
key, paid route or rate-limit bypass is used. Content-addressed game writes
and a manifest preserve the last good snapshot on a partial refresh failure.

The adapter links a tag to the next tracked field goal within 12 seconds and
discards links crossing shots, turnovers, free throws or period boundaries.
It reports FG% and points per FGA, **not full-possession PPP**. Named sets can
overlap. Shares are shares of linked tags, not coverage frequency across all
possessions. Provider detector accuracy varies by definition and team, so these
tags have zero forecast weight and become eligible only after their receipt.

## Shooting and pace checks

The common dynamics core uses beta-binomial 2P/3P/FT references and separates
positive progression pressure from negative regression pressure. Allowed-shot
references use actual opponents' earlier offense. Season references account
for returning-player minutes; recent-five references are disjoint from the
five games being assessed. Style quantities stay neutral; favorable/unfavorable
tones follow the team's scoring/prevention perspective. Pressure is points/100,
not a calibrated probability of improvement.

The dated validation record uses five-game nonoverlapping team targets, eight
training games minimum, and calibration before February 1, 2026. On 166 later
2025–26 windows, recent shooting-change MAE is 7.375 points/100, versus 8.241
for no change and **6.851 for a return to the earlier raw rate**. The new
posterior does not beat that stronger baseline. Pace MAE is 1.992 possessions/48
versus 2.013 for the season mean and 2.730 for last five.

These are retrospective date splits, not source-vintage replay. Feature design
was refined while viewing historical results. There are no complete current
five-game target windows yet, and no demonstrated incremental score-forecast
benefit. Thus pressure and tactical cells accompany the primary forecast as
diagnostics without automatically changing scores.

## Outputs and refresh

```powershell
python -m nbamodel.cli analyze --validate --out _evidence
python -m nbamodel.cli build --out _site
python -m pytest -q
python -m ruff check src tests scripts
```

`model_analysis.json` carries the two-season bank; `board.json` adds per-game
`model_diagnostics`, including personnel and coverage comparisons and a labeled
shooting scenario. The public factual slate/evidence remain on explicit contracts
that forbid these fields. The existing scheduled refresh now includes the public
tracking index and persists its cache with the game, coverage and Databallr caches.
