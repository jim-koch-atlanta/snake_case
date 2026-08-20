# Project Notes — Draft Copilot

**Snapshot: 2026-08-19. Draft: Friday 2026-08-28, 10:00 AM EDT — 9 days out.**

Running status of the build against the plan in `CLAUDE.md` and `README.md`.
Those two files are the *spec*; this file is the *state*. When they disagree,
this file is newer — fold the correction back into them.

---

## Status against the priority order

| # | Item | State |
|---|---|---|
| 1 | Pick-schedule generator (snake-with-holes) | **Done.** Tested, running on real league data, cross-checked against ESPN. |
| 2 | Player ID crosswalk | **Not started — now the critical path.** All input data is on disk. |
| 3 | Custom-scoring valuation | **Config done, no code.** All scoring values are real; the stat-line → points engine is unwritten. |
| 4 | DraftState + manual entry + UI | Not started. |
| 5 | VONA/survival + feasibility guard | Not started. Input source decided (league history, not ADP). |
| 6 | ESPN live-sync experiment | **Half answered.** Auth + shape verified; live-update behaviour still unknown. |
| 7 | League-history opponent priors | Not started. Data on disk and richer than expected. |
| 8 | LLM explanation layer | Not started. Still first to cut. |

**11 commits, clean tree, 24 tests passing, ruff clean.**

---

## What exists

```
engine/schedule.py        225 loc   pure snake-with-holes: keepers, collision cascade, traded picks
sources/league_config.py  250 loc   TOML -> engine inputs, all validation + loud errors
tests/test_schedule.py    231 loc   24 tests, hand-computed 4x3 examples + full 12x22
tools/poll_draft.py       180 loc   throwaway ESPN mDraftDetail poller (experiment #1)
tools/clean_fantasypros.py 134 loc  one-off: FantasyPros web-paste -> clean CSV
```

`engine/` has zero I/O and full test coverage (invariant #1). `sources/` holds
all parsing and validation (invariant #2). **`sources/league_config.py` has no
tests** — the one real coverage gap.

### Verified end-to-end on real league data

264 slots → 36 keepers → 228 live picks → 19 per team, all 12 teams.
My first pick is overall 6; my live-pick gaps run **5 to 24** (a pure snake
would be a flat 12). Three 24-pick waits and a two-pick stack five apart in
R19. This spread is the entire justification for the project.

---

## Data on hand

All under `data/` — **note `data/` is gitignored**, so none of it is committed.

| Path | Contents |
|---|---|
| `4for4/4for4_projections.csv` | 507 offense+K, stat-level, **has `ADP`** (274 populated) |
| `4for4/...-{db,dl,lb}-2026-table.csv` | ~920 IDP, stat-level, **`Tackles` and `Assists` separate** |
| `fantasypros/idp.csv` | raw web-page paste (do not parse directly) |
| `fantasypros/idp_clean.csv` | 204 IDP, cleaned: rank, player, team, pos, pos_rank, slot, bye, tier |
| `historical/draft-{2023,2024,2025}.csv` | 264 picks each, with `Position`, `Round`, populated `Keeper` (36/yr) |

Ignore every `FF Pts` column — that is the provider's scoring, not ours
(CLAUDE.md: always recompute from stat lines).

---

## Facts established since the spec was written

- **ESPN cookies work** (verified 2026-08-19) and `mDraftDetail` returns all
  264 slots pre-draft.
- **ESPN's grid does not reflect trades.** 254/264 slots match our generated
  schedule exactly; the 10 diffs are precisely our 10 traded picks, and ESPN
  shows the *original* owner every time.
- **ESPN `kona_player_info` returns stat-level projections** (~44 numeric stat
  ids) — a viable cross-check or fallback for 4for4.
- **Keeper inference is unnecessary.** CLAUDE.md speculated we might infer
  keepers by diffing prior rosters; the historical CSVs carry a populated
  `Keeper` column.
- **Reception scoring is 0.2, not half-PPR.** Docs corrected.
- **No IDP ADP exists anywhere**, and it is structural, not a timing artifact:
  offense ADP is already populated today, while 4for4's IDP tables have no ADP
  column at all. National ADP comes from redraft leagues that mostly don't
  start IDP. League history replaces it.

---

## Open questions and risks

1. **Does `mDraftDetail` update live, and at what latency?** Experiment #1,
   still unanswered — it needs a draft actually in progress, so it can only be
   tested against a mock before the 28th. If it doesn't update live, cut sync
   entirely and reclaim the time.
2. **Are the 10 trades registered in ESPN at all?** ESPN shows original owners.
   If ESPN doesn't know about the trades, it will put the wrong manager on the
   clock on draft day. Confirm with the commissioner.
3. **Keepers are not final.** Declarations are due ~2026-08-25. ESPN currently
   flags 9 slots `reservedForKeeper` (3 teams x 3) with `playerId: -1` and
   rounds that disagree with `league-config.toml`. Config is authoritative
   until declarations close; re-verify after.
4. **Cookies expire.** They are live now. Refresh the morning of the draft.
5. **`data/` is gitignored, including the crosswalk.** Invariant #3 calls for a
   hand-reviewed, frozen crosswalk — but as configured it will never be
   committed, so a lost working directory loses the hand review. Decide whether
   to except `data/crosswalk.csv` from the ignore. (`.gitignore` also has a
   duplicate `data/` entry and a stale comment claiming the rest of `data/` is
   tracked.)
6. **Crosswalk name matching will be the slow part.** 29 of 204 FantasyPros IDP
   names carry punctuation that breaks naive matching — `Tre'von Moehrig`,
   `Henry To'oTo'o`, `Kool-Aid McKinstry`, `Akeem Davis-Gaither`, plus many
   Jr./Sr./II/III. Historical CSVs also join name+team with a **non-breaking
   space** (`\xa0`) and carry the clean name in an *unnamed* column.
7. **`half_sack` is a half-sack unit.** ESPN's `HALFSK` = 1.4, so a full sack
   is 2.8. Projections report whole sacks. Getting this wrong undervalues a
   12-sack DL by ~17 points and reorders the whole DL board.

---

## Next steps, in order

1. **Run `tools/poll_draft.py` against a live mock draft.** Time-gated — it
   cannot be done after the 28th. Answers risk #1.
2. **Build the crosswalk** (priority #2). ESPN ids ↔ 4for4 names ↔ FantasyPros
   names ↔ historical names. Generate candidates, flag ambiguities, hand-review,
   freeze.
3. **Scoring engine** (priority #3): stat lines × `[scoring]` → points. Write
   the test first with a hand-computed example. IDP correctness over offense
   polish.
4. **Positional-timing priors from `data/historical/`** — smooth across all
   three years; per-year IDP counts swing enough (DE 20/26/20, DT 5/3/6) that
   any single season is noise.
5. Loader tests for `sources/league_config.py`.

---

## Notes for future readers

- Historical positions are **NFL** positions (DE/DT/CB/S/LB) and must be mapped
  to our roster slots (DL/LB/DB). `tools/clean_fantasypros.py` already does this
  mapping for the FantasyPros data — reuse it.
- `docs/league-config.toml` is canonical and gitignored (private league data);
  `docs/league-config.toml.example` is the committed template and is kept
  loadable, with 36 dummy keepers and a deliberate same-round collision.
- `docs/decisions.md` carries the *why* for the TOML migration, the 0.2
  reception scoring, the ADP substitution, and the keeper-inference call.
