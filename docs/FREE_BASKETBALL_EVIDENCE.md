# Free basketball evidence

The matchup research export now includes **2025–26 and 2026–27 separately** in
`evidence.json`. Selecting a year never silently substitutes another season.
Current fixtures and current injury reports remain current; historical team and
player statistics describe their actual historical club.

## Sources and boundaries

* [Databallr](https://databallr.com/stats): free public player/basic, advanced,
  team and opponent responses. Season IDs use the ending year. Team requests use
  `leverage=all` (all possessions). Raw responses, request URLs, UTC retrieval
  stamps and SHA-256 receipts are retained under `data/databallr/`. The advanced
  endpoint can return playoff rows despite `playoffs=false`; normalized regular
  season inputs explicitly filter `playoffs=0`. Public context contains only
  observed zone counts/rates, with Databallr's own zone names. Adjusted impact
  estimates remain research inputs and are not in the factual website payload.
* [NBA Inside the Game](https://www.nba.com/inside-the-game/team/zonedefense):
  measured zone/non-zone offense and defense, exact possessions, frequency,
  points per possession and turnovers. The public `zonedefenseteamleaders`
  endpoint is fetched without an account. **Non-zone does not mean verified man
  defense.** Coverage PPP ranks are recomputed with offense high/defense low;
  fewer than 20 coverage possessions remains visible but ungraded.
* Existing NBA public play-type/tracking tables and ESPN box scores/play by play
  continue to supply action efficiency and geometric shot zones. The Synergy
  public endpoint is requested per game: its POSS field is explicitly exported
  as `possessions_per_game`, never mislabeled as an exact cumulative count.

Source retrieval is not the last included game. Databallr supplies `updated_at`
but does not provide its final included game date. NBA coverage exposes games
and possessions plus our retrieval time. ESPN supplies the explicit observed
game cutoff. Each is labeled separately.

Refresh runs with the normal scheduled model build, with six-hour source caches.
Validation checks season, competition, all 30 clubs, counts, reconciliation and
coverage regressions before replacing a snapshot. A refused fetch retains the
dated previous snapshot. Receipt hashes are checked when reading Databallr.
Snapshots collected today cannot enter a backtest with an earlier `as_of`.

## What remains unavailable for free

Neither connected feed verifies drop, switch, blitz, hedge, ICE, press or named
offensive formations such as horns/5-out with possession-tagged efficiency.
Existing shot/matchup-based defensive style signals are **inferences**, separately
labeled; they must not be presented as measured coverage rates.

The free CBB sources BasketballQuant and CBBAnalytics provide observed team,
player, lineup and shot-zone context, but no verified two-season possession-tagged
man/zone or pick-and-roll coverage splits were found. Their defensive tendencies
may be compared without assigning fabricated scheme labels. No paid account,
trial, credentials or subscription is required by this integration.

Databallr is now a retained model research source and factual matchup input.
No claim is made that new impact estimates have retuned or improved forecast
coefficients: that requires a separate time-safe out-of-sample evaluation.
