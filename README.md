# Draft Copilot

Live draft-day decision support for a 12-team ESPN fantasy football league with
custom scoring (0.2 points per reception, full IDP) and round-based keepers.
Runs locally, renders a recommendation board on a second monitor, and stays
useful even when ESPN's API doesn't cooperate.

**Not** a general fantasy product. Purpose-built for one league, one draft
night, then archived.

## Why this exists

- The league starts 6 IDP players — half the lineup — and no commercial draft
  tool models IDP survival probability or keeper-distorted pick schedules well.
- 3 keepers/team kept in their original round means the draft is a snake with
  36 holes in it. Pick gaps vary wildly by round; generic ADP math is wrong.
- Same 12 managers for years + ESPN's historical draft API = we can model the
  actual humans, not a generic room.
- Scoring is league-specific, not a preset. Receptions are worth **0.2** here —
  not standard, not half-PPR, not full PPR — so every public ranking, ADP-derived
  value and "expert" tier is computed against the wrong scoring. Player values
  are recomputed from stat-level projections using the rules in
  `docs/league-config.toml`; change `receiving.reception` and the whole board
  re-derives. Nothing in the engine hardcodes a PPR assumption.

## Quick start

```bash
uv sync
cp .env.example .env        # add SWID / espn_s2 / LEAGUE_ID
# Fill docs/league-config.toml (roster/order/trades done; add keepers + scoring)
uv run pytest
uv run python -m app.serve  # http://localhost:8000
```

## Layout

```
engine/     pure valuation + draft-state logic (no I/O, fully tested)
sources/    ESPN API, 4for4 CSV loader, Sleeper, crosswalk builder
app/        FastAPI + one HTML page (htmx), manual pick entry + board
tools/      mock-draft recorder/replayer, one-off scripts
data/       crosswalk.csv, projection snapshots, league history dumps
docs/       league-config.toml (canonical league facts), decisions log
```

## Draft-night runbook (draft − 0)

1. Morning: re-export 4for4 CSVs, refresh ESPN cookies, `uv run pytest`.
2. Load final projections, regen valuations, eyeball top-30 sanity.
3. T−30min: start server, enter any last-minute keeper changes.
4. During draft: manual-enter every pick as it happens (ESPN sync, if enabled,
   reconciles behind it). Read the board; the tool proposes, you decide.
5. If anything breaks: the CSV cheat sheet exported at step 2 is the fallback.
   Print it.

## Status

See CLAUDE.md → Priority order, and PROJECT_NOTES.md for the running state.

- **1 Pick-schedule generator** — done, verified against real league data.
- **2 Player crosswalk** — built and keyed on ESPN player ids; **awaiting hand
  review** of `data/crosswalk_review.csv` before it can be frozen.
- **3 Custom-scoring valuation** — scoring engine done; the projection loader
  that feeds it stat lines is next.
- **4 DraftState** — append-only event log and reconciliation done; manual
  entry and the UI are not started.
- **5–8** — not started. #8 (LLM explanations) is still first to cut.
