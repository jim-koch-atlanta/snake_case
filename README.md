# Draft Copilot

Live draft-day decision support for a 12-team ESPN fantasy football league with
half-PPR + IDP scoring and round-based keepers. Runs locally, renders a
recommendation board on a second monitor, and stays useful even when ESPN's
API doesn't cooperate.

**Not** a general fantasy product. Purpose-built for one league, one draft
night, then archived.

## Why this exists

- The league starts 6 IDP players — half the lineup — and no commercial draft
  tool models IDP survival probability or keeper-distorted pick schedules well.
- 3 keepers/team kept in their original round means the draft is a snake with
  36 holes in it. Pick gaps vary wildly by round; generic ADP math is wrong.
- Same 12 managers for years + ESPN's historical draft API = we can model the
  actual humans, not a generic room.

## Quick start

```bash
uv sync
cp .env.example .env        # add SWID / espn_s2 / LEAGUE_ID
# Fill docs/league-config.md (roster slots done; add keepers, order, scoring)
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
docs/       league-config.md (canonical league facts), decisions log
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

See CLAUDE.md → Priority order. Currently at: **1**.
