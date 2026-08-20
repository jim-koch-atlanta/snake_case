# Draft Copilot — CLAUDE.md

Fantasy football draft-day decision support tool for a 12-team ESPN league.
**Hard deadline: the live draft is Friday, August 28, 2026. Days remaining matter more than elegance.**

## What this is (and is not)

- A **deterministic valuation + draft-state engine** with a thin local web UI, used live on a second monitor during a snake draft.
- The LLM (Claude API) is **never on the hot path**. It may be used for: parsing config, explaining a recommendation, answering "why not player X?" between picks. The pick recommendation itself must be pure, testable Python math.
- **Local `DraftState` is the source of truth.** ESPN polling *reconciles into* it. Manual pick entry must always work standalone — ESPN sync is a convenience layer that can fail without taking the tool down.

## League facts (canonical — do not invent)

- 12 teams, snake draft, order already set (see `docs/league-config.toml`)
- Starters: 1 QB, 1 RB, 1 RB/WR, 3 WR/TE, 2 DL, 2 LB, 2 DB, 1 K
- Roster: 13 starters + 9 BE + 3 IR = 25 spots per team. IR is not drafted, so 22 *draftable* slots → 22 rounds × 12 teams = 264 total draft slots. After 3 keepers, 19 live picks per team.
- **Keepers: 3 per team, kept in the round originally drafted.** 36 of 264 slots are pre-assigned. The pick schedule is NOT a pure snake — it must be generated from the keeper assignments in `docs/league-config.toml`.
  - **Round collisions:** if a team keeps two players originally drafted in the same round X, one is kept at the round-X pick and the next backfills round X−1. Cascade one round earlier for each additional colliding keeper (so three keepers from round X occupy X, X−1, X−2). Always shift to the *earlier* round.
- Scoring: custom offense + IDP, defined entirely in `docs/league-config.toml`. **Never use pre-computed fantasy points from any external source — always recompute from stat-level projections using our scoring.**
- **Points per reception is a config value, not a mode.** This league is **0.2 per reception** — it is *not* half-PPR, and nothing in the code may assume 0.5, 1.0, or a "PPR / half-PPR / standard" preset. The value lives at `[scoring] receiving.reception` and flows through the same stat-line × scoring-rule path as every other stat. Changing that one number must re-derive every valuation with no other edit.
  - Consequence to keep in mind: at 0.2, a 100-catch season is worth 20 points, not 50. Reception volume separates WRs far less than in a half-PPR league, so target-hog possession receivers compress toward the pack and the WR/TE replacement baselines sit closer together. Do not import PPR-flavored rankings or tiers from anywhere.

## Architecture invariants

1. `engine/` contains zero I/O. Pure functions: `(DraftState, projections, config) -> recommendations`. Everything in `engine/` has unit tests.
2. `sources/` contains all external I/O (ESPN API, CSV loaders, Sleeper). Each source returns normalized dataclasses keyed by **our internal player_id**, never by source-native IDs.
3. The player ID crosswalk (`data/crosswalk.csv`) is built once, reviewed by hand, and **frozen**. Any unmatched player at load time is a hard error printed loudly, not a silent drop.
4. `DraftState` is an append-only event log of picks (`(overall_pick, team_id, player_id, source)` where source ∈ {keeper, manual, espn_sync}). State is derived by replay. Manual and ESPN events reconcile by overall_pick; **manual wins conflicts**.
5. The UI is one locally-served page. No build step. htmx or vanilla JS + fetch polling. Do not introduce React/Vite here — wrong tool for a 10-day disposable.

## Valuation model (the actual product)

- **VOR baseline from OUR lineup**, per position, 12 teams: replacement ≈ RB19-20 (1 slot + flex share), WR ≈ WR41 (3 WR/TE slots + flex share), TE competes with WR38-41 (expect ~top-4 TEs only to clear the bar), QB12, K12, DL24, LB24, DB24. These ranks come from *slot counts*, so they hold regardless of scoring — but the point *spread* between a baseline and the players above it is scoring-dependent, and at 0.2/reception the WR spread is flatter than PPR intuition suggests (see League facts).
- **VONA / survival model**: for each candidate, P(survives to my next live pick) from keeper-adjusted ADP (drop the 36 keepers, re-rank survivors, map rank → live pick number) with per-position σ. **Widen σ substantially for IDP** — IDP ADP is thin and noisy.
- Monte Carlo the intervening picks (~1000 runs). Opponent rosters constrain positional need — keeper sets are known, so filled slots are hard constraints, not priors.
- Recommendation = maximize `value(now) − E[best available at that position at my next pick]`, subject to the **legal-lineup feasibility guard**: `picks_remaining − mandatory_unfilled_slots` must never go negative. `mandatory_unfilled_slots` counts *every* still-unfillable starting slot — QB, RB, RB/WR, 3×WR/TE, 2DL, 2LB, 2DB, K (all 13 starters) — where a flex slot (RB/WR, WR/TE) is satisfied by any eligible position already rostered. In practice RB/WR/TE are over-drafted and never the binding constraint; QB, K, and the 6 IDP slots are what actually trip this. Surface it in the UI, red at 0.
- Late rounds (picks where all mandatory slots are fillable): stop optimizing EV, prefer variance/upside (bench is 9+3 IR vs only 6 offensive starters — stashes are cheap).

## Data sources

- **4for4 (subscriber)**: manual CSV export, retrieved by hand (no API, no scraping). Source: `https://www.4for4.com/fantasy-football-projections/qb/2026` (swap the position in the path for the other tables). Local, retrieved 2026-08-19:
  - `data/4for4/4for4_projections.csv` — offense + K (QB/RB/WR/TE/K, ~507 players), stat-level, **and carries an `ADP` column** (~274 populated).
  - `data/4for4/4for4-fantasy-football-projections-{db,dl,lb}-2026-table.csv` — IDP, ~920 players, stat-level: `Tackles` (solo) and `Assists` as **separate** columns, plus Sacks, TFL, QBH, INT, PD, FFum, FR, Safety, DefTD.
  - Every file also has an `FF Pts` column — **ignore it**, it is 4for4's scoring, not ours.
- **FantasyPros IDP consensus**: `https://www.fantasypros.com/nfl/rankings/idp-cheatsheets.php` → `data/fantasypros/idp.csv`. Intended as an IDP blend/sanity check; 4for4 already covers IDP stat-level, so this is a nice-to-have, not a dependency.
- **League history**: our league's own archives → `data/historical/draft-{2023,2024,2025}.csv`. 264 picks each, with `Position`, `Round`, and a populated `Keeper` column (36/year). Columns: `NO., PLAYER (name+team, NBSP-separated), <unnamed> (clean name), Position, Round, Keeper`. Positions are real NFL positions (DE/DT/CB/S/LB), not our roster slots — needs mapping to DL/LB/DB.
- **ESPN v3 API**: `https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{year}/segments/0/leagues/{league_id}` with views `mDraftDetail`, `mSettings`, `mTeam`, `kona_player_info`. Auth = `SWID` + `espn_s2` cookies from env (`.env`, git-ignored). Cookies expire — refresh morning of draft.
  - **VERIFIED 2026-08-19**: `mDraftDetail` returns all 264 slots pre-draft with `teamId` per slot; 254/264 match our generated schedule exactly. The 10 diffs are precisely our 10 traded picks, and ESPN shows the **original** owner in every case — *ESPN's grid does not reflect trades*. Picks carry `keeper` and `reservedForKeeper` flags; as of 2026-08-19 nine slots are `reservedForKeeper` (3 teams x 3) with `playerId: -1` and rounds that disagree with our config. Treat `docs/league-config.toml` as authoritative for keepers.
  - **VERIFIED 2026-08-19**: `kona_player_info` returns stat-level projections (`statSourceId=1`) as ~44 numeric ESPN stat ids, plus prior-season actuals. Usable as a cross-check or fallback for 4for4.
  - **STILL UNVERIFIED**: whether `mDraftDetail` updates *live* during an active draft, and at what latency. This is experiment #1 — run `tools/poll_draft.py` against a mock draft. If it doesn't update live, manual entry is the primary path and we cut sync entirely.
- **Sleeper public API** (no auth): injury status / news backup; its player DB is also a useful crosswalk aid.

## Commands

```bash
uv run pytest                      # engine tests — must stay green
uv run python -m sources.build_crosswalk   # regen crosswalk candidates for hand review
uv run python -m app.serve         # local UI at :8000
uv run python -m tools.mock_replay <draft_json>  # replay a recorded mock draft through the engine
```

## Conventions

- Python 3.12, `uv`, `ruff`, dataclasses (not pydantic — no validation-heavy API surface here), pytest.
- Type hints everywhere in `engine/`. `sources/` can be looser.
- No premature abstraction. This code is disposable after 2026-08-29. Duplicated code beats a framework.
- When touching the valuation math, write the test first with a hand-computed tiny example (4 teams, 3 rounds).

## Priority order (when in doubt, work on the lowest unfinished number)

1. Pick-schedule generator from keeper config (the snake-with-holes). Everything depends on it.
2. Player ID crosswalk.
3. Custom-scoring valuation from stat-level projections (IDP correctness > offense polish — half the lineup is IDP and that's the edge).
4. DraftState + manual pick entry + minimal UI.
5. VONA/survival engine + feasibility guard.
6. ESPN live-sync experiment, then integration only if it works.
7. League-history opponent priors.
8. LLM explanation layer. **Cut this first if behind.**
