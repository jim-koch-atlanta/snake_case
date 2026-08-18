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
