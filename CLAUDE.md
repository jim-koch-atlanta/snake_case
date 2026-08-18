# Draft Copilot — CLAUDE.md

Fantasy football draft-day decision support tool for a 12-team ESPN league.
**Hard deadline: the live draft is Friday, August 28, 2026. Days remaining matter more than elegance.**

## What this is (and is not)

- A **deterministic valuation + draft-state engine** with a thin local web UI, used live on a second monitor during a snake draft.
- The LLM (Claude API) is **never on the hot path**. It may be used for: parsing config, explaining a recommendation, answering "why not player X?" between picks. The pick recommendation itself must be pure, testable Python math.
- **Local `DraftState` is the source of truth.** ESPN polling *reconciles into* it. Manual pick entry must always work standalone — ESPN sync is a convenience layer that can fail without taking the tool down.

## League facts (canonical — do not invent)

- 12 teams, snake draft, order already set (see `docs/league-config.md`)
- Starters: 1 QB, 1 RB, 1 RB/WR, 3 WR/TE, 2 DL, 2 LB, 2 DB, 1 K
- Roster: 13 starters + 9 BE + 3 IR = 25 spots per team. IR is not drafted, so 22 *draftable* slots → 22 rounds × 12 teams = 264 total draft slots. After 3 keepers, 19 live picks per team.
- **Keepers: 3 per team, kept in the round originally drafted.** 36 of 264 slots are pre-assigned. The pick schedule is NOT a pure snake — it must be generated from the keeper assignments in `docs/league-config.md`.
  - **Round collisions:** if a team keeps two players originally drafted in the same round X, one is kept at the round-X pick and the next backfills round X−1. Cascade one round earlier for each additional colliding keeper (so three keepers from round X occupy X, X−1, X−2). Always shift to the *earlier* round.
- Scoring: half-PPR offense + IDP. Exact scoring rules live in `docs/league-config.md`. **Never use pre-computed fantasy points from any external source — always recompute from stat-level projections using our scoring.**

## Architecture invariants

1. `engine/` contains zero I/O. Pure functions: `(DraftState, projections, config) -> recommendations`. Everything in `engine/` has unit tests.
2. `sources/` contains all external I/O (ESPN API, CSV loaders, Sleeper). Each source returns normalized dataclasses keyed by **our internal player_id**, never by source-native IDs.
3. The player ID crosswalk (`data/crosswalk.csv`) is built once, reviewed by hand, and **frozen**. Any unmatched player at load time is a hard error printed loudly, not a silent drop.
4. `DraftState` is an append-only event log of picks (`(overall_pick, team_id, player_id, source)` where source ∈ {keeper, manual, espn_sync}). State is derived by replay. Manual and ESPN events reconcile by overall_pick; **manual wins conflicts**.
5. The UI is one locally-served page. No build step. htmx or vanilla JS + fetch polling. Do not introduce React/Vite here — wrong tool for a 10-day disposable.

## Valuation model (the actual product)

- **VOR baseline from OUR lineup**, per position, 12 teams: replacement ≈ RB19-20 (1 slot + flex share), WR ≈ WR41 (3 WR/TE slots + flex share), TE competes with WR38-41 (expect ~top-4 TEs only to clear the bar), QB12, K12, DL24, LB24, DB24.
- **VONA / survival model**: for each candidate, P(survives to my next live pick) from keeper-adjusted ADP (drop the 36 keepers, re-rank survivors, map rank → live pick number) with per-position σ. **Widen σ substantially for IDP** — IDP ADP is thin and noisy.
- Monte Carlo the intervening picks (~1000 runs). Opponent rosters constrain positional need — keeper sets are known, so filled slots are hard constraints, not priors.
- Recommendation = maximize `value(now) − E[best available at that position at my next pick]`, subject to the **legal-lineup feasibility guard**: `picks_remaining − mandatory_unfilled_slots` must never go negative. `mandatory_unfilled_slots` counts *every* still-unfillable starting slot — QB, RB, RB/WR, 3×WR/TE, 2DL, 2LB, 2DB, K (all 13 starters) — where a flex slot (RB/WR, WR/TE) is satisfied by any eligible position already rostered. In practice RB/WR/TE are over-drafted and never the binding constraint; QB, K, and the 6 IDP slots are what actually trip this. Surface it in the UI, red at 0.
- Late rounds (picks where all mandatory slots are fillable): stop optimizing EV, prefer variance/upside (bench is 9+3 IR vs only 6 offensive starters — stashes are cheap).

## Data sources

- **4for4 (subscriber)**: manual CSV export the night before + morning of. Loader must tolerate their column naming. No scraping.
- **ESPN v3 API**: `https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{year}/segments/0/leagues/{league_id}` with views `mDraftDetail`, `mSettings`, `mTeam`, `kona_player_info`. Auth = `SWID` + `espn_s2` cookies from env (`.env`, git-ignored). Cookies expire — refresh morning of draft.
  - **UNVERIFIED**: whether `mDraftDetail` updates live during an active draft, and at what latency. This is experiment #1. If it doesn't, manual entry is the primary path and we cut sync entirely.
- **League history**: same endpoint, prior seasons — per-manager draft priors (reach vs ADP, positional order) and the ADP→actual-pick regression. Check whether pick objects carry a keeper flag; if not, infer keepers by diffing against prior end-of-season rosters.
- **Sleeper public API** (no auth): injury status / news backup.
- **FantasyPros IDP consensus**: manual CSV for IDP blend.

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
