# Decisions log

Short entries. Date, decision, why. Claude Code: read this before proposing
architecture changes; append when we make a call.

## 2026-08-18 — Deterministic engine, LLM off the hot path
90s pick clock can't absorb LLM latency/variance. Engine is pure Python,
unit-tested. LLM = config parsing + explanations between picks only.

## 2026-08-18 — Local DraftState is source of truth; ESPN reconciles into it
ESPN live-draft API behavior is unverified and cookies can die mid-draft.
Manual entry is the primary input path. Event log, replay-derived state,
manual wins conflicts.

## 2026-08-18 — No MCP servers for v1
Original idea was ESPN/4for4 MCPs. Cut: 4for4 has no API (subscriber CSV
export instead), and ESPN is a plain HTTP client inside `sources/`. MCP adds
a protocol layer with no second consumer. Revisit only if this outlives the
season.

## 2026-08-18 — Pick schedule generated from keeper table, not snake formula
3 keepers/team kept in original round = 36 pre-assigned slots. Gaps between
my live picks vary by round; the whole survival model keys off this.

## 2026-08-18 — Recompute points from stat-level projections
Custom IDP scoring means pre-computed fantasy points from any source are
wrong for this league. Always stat lines × our scoring rules.

## 2026-08-18 — htmx/vanilla UI, no React
Disposable single-page local tool, 10-day budget. (Yes, despite the CYODC
React/Vite muscle memory — different project shape.)

## 2026-08-18 — Frozen hand-reviewed player crosswalk
ESPN IDs ≠ 4for4 names ≠ FantasyPros names. Build once, review, freeze.
Unmatched player at load = loud hard error.

## 2026-08-19 — League config moved from Markdown to TOML
Markdown table parsing silently dropped malformed rows (verified: 5 realistic
typos each produced 0 parsed trades, no error). A dropped keeper/trade row
means a wrong pick schedule with nothing visibly broken. TOML gives typed
values, a hard error on malformed syntax, and keeps comments (which JSON would
have forced out of a file whose warnings matter). `tomllib` is stdlib — no new
dependency. `docs/league-config.md` deleted; `.toml` is canonical.

## 2026-08-19 — Reception scoring is 0.2, not half-PPR
Docs claimed half-PPR; ESPN league settings say 0.2/reception. Points per
reception is a config value at `[scoring] receiving.reception`, never a
preset. At 0.2 a 100-catch season is 20 points, not 50, so WR spread is much
flatter than PPR intuition — no PPR-flavored rankings or tiers may be imported.

## 2026-08-19 — No IDP ADP exists; use our own draft history instead
Could not source ADP with IDP coverage anywhere. 4for4's ADP column is offense
+ K only. Substitute: `data/historical/draft-{2023,2024,2025}.csv` — 792 real
picks from THIS league, with position and round, including IDP. Better than
generic ADP anyway: it measures when these 12 managers actually take each
position, which is what the survival model needs. Offense keeps 4for4 ADP as a
cross-check. Historical positions are NFL positions (DE/DT/CB/S) and must be
mapped to our DL/LB/DB slots.

## 2026-08-19 — Keeper inference from history is unnecessary
The historical CSVs carry a populated `Keeper` column (36 per year, matching
3/team). No need to diff against prior end-of-season rosters as CLAUDE.md
originally speculated.

## 2026-08-20 — CUT ESPN live draft sync. Manual entry is the only input path.
Experiment #1 run against a live ESPN mock draft (league 1596648425, a clone of
our league). With the browser at pick 67 of 264, the read API reported
`picks with a real playerId: 0` and `rostered players: 0`. Checked
`mDraftDetail`, `mRoster`, `mTeam`, `mMatchup`, `mStatus`, `mSettings` — every
one blind to the in-progress draft. Re-verified after refreshing the espn_s2
cookie, so this is not an auth artifact.

`lm-api-reads.fantasy.espn.com` is a READ REPLICA and does not carry in-flight
draft state. ESPN's own draft client uses a separate live channel we are not
going to reverse-engineer with 8 days left.

What still works and is worth keeping:
  - `inProgress` DOES flip true, so we can detect that a draft is live.
  - The pre-draft 264-slot grid with per-slot `teamId` is available and already
    validated our generated schedule (254/264, the 10 diffs being our trades).

Consequences:
  - Priority #6 (live sync integration) is CUT. Do not build it.
  - Manual pick entry is the ONLY input path and must be excellent: fast entry,
    undo, and a visible "picks entered vs picks elapsed" counter.
  - DraftState keeps its `espn_sync` source and precedence rules — harmless,
    already tested, and it costs nothing to leave the seam in place.
  - `tools/poll_draft.py` has served its purpose; keep it only for a post-draft
    reconciliation pass (the API should show final results once `drafted=True`).
