# Project Notes — Draft Copilot

**Snapshot: 2026-08-19 (updated after unattended session). Draft: Friday
2026-08-28, 10:00 AM EDT — 9 days out.**

Running status of the build against the plan in `CLAUDE.md` and `README.md`.
Those two files are the *spec*; this file is the *state*. When they disagree,
this file is newer — fold the correction back into them.

---

## Status against the priority order

| # | Item | State |
|---|---|---|
| 1 | Pick-schedule generator (snake-with-holes) | **Done.** Tested, running on real league data, cross-checked against ESPN. |
| 2 | Player ID crosswalk | **Built, awaiting hand review.** 1979 auto-matched, 83 in `data/crosswalk_review.csv`. NOT frozen. |
| 3 | Custom-scoring valuation | **Scoring engine done** (`engine/scoring.py`), tests hand-computed. Projection loader (4for4 columns → canonical stats) not written. Kicker scoring blocked — see session log. |
| 4 | DraftState + manual entry + UI | **Core done** (`engine/draft_state.py`): event log, replay, reconciliation. Manual entry and UI not started, deliberately. |
| 5 | VONA/survival + feasibility guard | Not started. Input source decided (league history, not ADP). |
| 6 | ESPN live-sync experiment | **DONE — answered NO, feature CUT.** The read API is blind to an in-progress draft (browser at pick 67, API reported 0). Manual entry is the only input path. |
| 7 | League-history opponent priors | Not started. Data on disk and richer than expected. |
| 8 | LLM explanation layer | Not started. Still first to cut. |

**18 commits, clean tree, 173 tests passing, ruff clean.**

---

## What exists

```
engine/schedule.py          pure snake-with-holes: keepers, collision cascade, traded picks
engine/positions.py         NFL position -> roster slot, shared by 3+ callers
engine/scoring.py           stat line x [scoring] -> points; half-sack + 0.2/reception
engine/draft_state.py       append-only pick log, replay, manual>keeper>espn_sync
sources/league_config.py    TOML -> engine inputs, all validation + loud errors
sources/build_crosswalk.py  ESPN-id-keyed crosswalk + review queue
tools/poll_draft.py         throwaway ESPN mDraftDetail poller (experiment #1)
tools/clean_fantasypros.py  one-off: FantasyPros web-paste -> clean CSV
```

`engine/` has zero I/O and full test coverage (invariant #1). `sources/` holds
all parsing and validation (invariant #2). The former loader coverage gap is
closed — `tests/test_league_config.py` now has 30 tests.

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
| `crosswalk.csv` | 1979 auto-matched source→ESPN-id rows. **Committed** (the one exception to the `data/*` ignore) |
| `crosswalk_review.csv` | 83 rows needing hand review. Not committed |
| `espn/players.json`, `espn/proteams.json` | cached ESPN spine (2472 players) + team-id map |

Ignore every `FF Pts` column — that is the provider's scoring, not ours
(CLAUDE.md: always recompute from stat lines).

---

## Facts established since the spec was written

- **ESPN cookies work** and `mDraftDetail` returns all 264 slots pre-draft —
  but it does **not** update during a live draft (verified 2026-08-20 against a
  mock at pick 67). `inProgress` does flip true, so liveness is detectable even
  though picks are not.
- **ESPN's grid does not reflect trades.** 254/264 slots match our generated
  schedule exactly; the 10 diffs are precisely our 10 traded picks, and ESPN
  shows the *original* owner every time.
- **ESPN `kona_player_info` returns stat-level projections** (~44 numeric stat
  ids) — a viable cross-check or fallback for 4for4.
- **Keeper inference is unnecessary.** CLAUDE.md speculated we might infer
  keepers by diffing prior rosters; the historical CSVs carry a populated
  `Keeper` column.
- **Reception scoring is 0.2, not half-PPR.** Docs corrected, and now enforced
  by test (`tests/test_scoring.py` asserts 100 catches = 20 points and
  explicitly that it is not the half-PPR or full-PPR total).
- **ESPN position ids** decoded empirically from real data: `1=QB 2=RB 3=WR
  4=TE 5=K 9=DT 10=DE 11=LB 12=CB 13=S`. Player records carry only a numeric
  `proTeamId`; the id→abbreviation map comes from `?view=proTeamSchedules_wl`
  (33 entries including FA) rather than being hardcoded.
- **Our scoring genuinely reorders the providers' boards.** 4for4's LB2 is
  Jordyn Brooks; under our rules it is Cedric Gray (190.4 to Brooks' 188.0).
  That is the whole premise of recomputing from stat lines, now demonstrated
  rather than assumed.
- **Name collisions are real and dangerous.** The ESPN universe contains two
  Lamar Jacksons (QB and CB) and two Justin Jeffersons (WR and LB), and
  4for4 lists Travis Hunter as CB where ESPN has him at WR. All three are in
  the review queue rather than silently matched.
- **No IDP ADP exists anywhere**, and it is structural, not a timing artifact:
  offense ADP is already populated today, while 4for4's IDP tables have no ADP
  column at all. National ADP comes from redraft leagues that mostly don't
  start IDP. League history replaces it.

---

## Open questions and risks

1. ~~Does `mDraftDetail` update live?~~ **ANSWERED 2026-08-20: no.** Tested
   against a live mock at pick 67 of 264 — `mDraftDetail`, `mRoster`, `mTeam`
   and `mMatchup` all reported zero picks and zero rostered players, with fresh
   cookies. Live sync is CUT (see docs/decisions.md). **New risk in its place:
   manual entry is now the single point of failure for draft input** — it must
   be fast, undoable, and show a picks-entered-vs-picks-elapsed counter so a
   missed entry is visible immediately.
2. **Are the 10 trades registered in ESPN at all?** ESPN shows original owners.
   If ESPN doesn't know about the trades, it will put the wrong manager on the
   clock on draft day. Confirm with the commissioner.
3. **Keepers are not final.** Declarations go into ESPN ~2026-08-24/25. ESPN
   currently flags 9 slots `reservedForKeeper` (3 teams x 3) with
   `playerId: -1` and rounds that disagree with `league-config.toml`. Config
   stays authoritative (DraftState enforces keeper > espn_sync). **Once ESPN is
   populated, diff config vs ESPN keepers** — agreement validates both, and any
   disagreement needs resolving before draft day.
4. **Cookies expire.** They are live now. Refresh the morning of the draft.
5. ~~Kicker scoring unresolved~~ **RESOLVED 2026-08-20.** `fgy` is ESPN's "FG
   Made Yards" — points per FG *yard*. Yardage comes from `kona_player_info`
   **stat id 214** (decode verified across 8 kickers). Standing consequence:
   **kickers must be sourced from ESPN, not 4for4**, which has FG counts but no
   yardage. Every other position can come from 4for4.
6. **The crosswalk is not frozen.** 83 rows in `data/crosswalk_review.csv` need
   hand review before invariant #3 is satisfied. Until then any consumer must
   treat an unmatched player as a hard error, not a silent drop.
7. **`half_sack` is a half-sack unit.** Handled in `engine/scoring.py` and
   asserted by test, but it stays on this list because any *new* code path that
   reads sacks must apply the ×2 conversion. Myles Garrett's 14.6 sacks are
   40.9 points converted, 20.4 unconverted.
8. **ESPN's player endpoint has sharp edges.** `kona_player_info` returns HTTP
   400 without a sort key (`sortPercOwned`) regardless of limit, and the full
   universe is 2472 players — a limit of 2000 silently truncates and tripled the
   unmatched count. Both handled in `sources/build_crosswalk.py`; noted here
   because any future ESPN caller will hit them.

---

## Next steps, in order

1. **Projection loader**: 4for4 columns → canonical stat names → crosswalk →
   the scoring engine, producing a valued player pool keyed by ESPN id.
   Kickers come from ESPN (stat id 214), everything else from 4for4. This is
   the blocker — nothing downstream can start without a valued pool.
2. **VOR baselines** off that pool. Note the DL/LB baselines must be recomputed
   using ESPN's slotting, not 4for4's: 15 edge rushers (Parsons, Watt, Mack,
   Gary, Chubb...) are LB in 4for4 and DL in ESPN, and ESPN governs lineup
   legality.
3. **Freeze the crosswalk** — the review is merged, so invariant #3 is
   satisfiable now.
4. **Manual pick entry + UI.** Now the only input path, so it carries more
   weight than originally planned.
5. **Positional-timing priors from `data/historical/`** — smooth across all
   three years; per-year IDP counts swing enough (DE 20/26/20, DT 5/3/6) that
   any single season is noise.

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
