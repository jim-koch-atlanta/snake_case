# Session log

Append-only. What was done, what was learned, what needs a human.

---

## 2026-08-19 — Unattended session: positions, loader tests, crosswalk, scoring, DraftState

Tasks 1–6 all completed. **173 tests passing** (was 24), ruff clean, tree clean,
7 commits.

### 1. Shared position mapping — `engine/positions.py`

Promoted the NFL-position → roster-slot map out of `tools/clean_fantasypros.py`.
Lives in `engine/` (not `sources/`) because the scoring engine needs it and
`engine/` must never import from `sources/`.

Full vocabulary observed across all data files:

| source | positions |
|---|---|
| `data/historical/` | WR RB TE QB K LB DE S DT CB |
| 4for4 offense | QB RB WR TE K |
| 4for4 IDP | CB S DE DT LB |
| FantasyPros | LB DE S CB DT |

Mapping: `DE/DT→DL`, `CB/S→DB`, `LB→LB`, offense maps to itself. The five
offensive positions appear in historical drafts but never in IDP data — covered
by tests as instructed.

Unknown positions raise `UnknownPositionError` rather than being guessed.
**`EDGE` and `D/ST` are deliberately unmapped**: EDGE is scheme-dependent
(DL or LB) and this league has no D/ST slot. I did add unambiguous aliases not
currently present in any file (`NT→DL`, `MLB/ILB/OLB→LB`, `FS/SS→DB`, `PK→K`)
so a source spelling a position differently degrades to a correct answer rather
than a hard failure mid-draft. Flagging in case you'd rather they also raise.

`tools/clean_fantasypros.py` now uses the shared module and produces a
byte-identical `idp_clean.csv` (verified by diff).

### 2. Loader tests — `tests/test_league_config.py`

30 tests against `docs/league-config.toml.example`, closing the known gap.
Covers the collision cascade (Team Name 3: declared R7,R7,R16 → occupies
R7,R6,R16), traded-pick ownership, and every validation path, asserting each
error names the offending key path, row index, or team.

Two of my own test-construction bugs surfaced and were fixed in the tests, not
the implementation: the mutation string `traded_picks = [` also matches a
*comment* line above the array, so both mutations were silently hitting the
comment. Anchored to line start. No implementation defect was found.

### 3. Crosswalk — `sources/build_crosswalk.py`

Spine is the ESPN player id. Results:

| source | rows | matched | review | unmatched |
|---|---|---|---|---|
| 4for4 offense | 507 | 496 | 10 | 1 |
| 4for4 IDP | 923 | 885 | 32 | 6 |
| FantasyPros IDP | 204 | 189 | 15 | 0 |
| historical | 428 | 409 | 14 | 5 |
| **TOTAL** | **2062** | **1979** | **71** | **12** |

83 rows need review, broken down as: 30 below the auto floor, 21 ambiguous
names, 20 slot mismatches, 12 with no candidate at all.

Normalization handles the `\xa0` name+team join in `data/historical/` (clean
name is in the unnamed column), apostrophes and internal capitals, hyphens, and
Jr./Sr./II/III/IV/V. Verified end to end: `Tre'von Moehrig`, `Henry To'oTo'o`,
`Kool-Aid McKinstry`, `Akeem Davis-Gaither`, `Amon-Ra St. Brown`,
`D'Angelo Ponds`, `Dexter Lawrence II` all auto-matched correctly.

Auto-match requires an unambiguous candidate scoring ≥0.94 **and** roster-slot
agreement. Everything else goes to review, per instruction. Real hazards it
caught: **two Lamar Jacksons** (a QB and a CB), **two Justin Jeffersons** (WR
and LB), and **Travis Hunter** listed CB by 4for4 but WR by ESPN.

Team abbreviations are used only to order candidates within a tie, never as a
match key. Sources disagree (`WSH`/`WAS`, `JAX`/`JAC`), so there's an alias
table.

**Learned along the way:**
- `kona_player_info` **requires a sort key** — without `sortPercOwned` it
  returns HTTP 400 regardless of limit. Cost me three attempts.
- The full ESPN player universe is **2472**, not 2000. My first run capped at
  2000 and produced 36 unmatched; raising the limit cut that to 12.
- ESPN position ids decoded empirically: `1=QB 2=RB 3=WR 4=TE 5=K 9=DT 10=DE
  11=LB 12=CB 13=S`.
- Player records carry only a numeric `proTeamId`. Rather than hardcode 32
  teams I pull the authoritative map from
  `?view=proTeamSchedules_wl` (33 entries incl. FA) and cache it.

**NOT frozen**, as instructed.

### 4. Scoring engine — `engine/scoring.py`

Tests written first, all hand-computed. Pure, I/O-free; the caller passes the
scoring table so changing one value re-derives everything.

The three required cases, all passing:
- **high-reception WR**: 100 rec × 0.2 + 1200 yds × 0.1 + 8 TD × 6.0 = **188.0**,
  asserted exactly, plus explicit assertions that it is *not* the half-PPR
  (218.0) or full-PPR totals.
- **12-sack DL**: 12 sacks = 24 half-sack units × 1.4 = **33.6**, with an
  explicit assertion that it is not 16.8.
- **tackle-heavy LB**: 100 solo × 1.1 + 40 assists × 0.8 = **142.0**, asserting
  it is neither 154.0 (summed as all-solo) nor 112.0 (summed as all-assist).

`units_per_stat` is 1.0 for every rule except `sacks` (2.0) — a test asserts
that sacks is the *only* multiplier, so the half-sack conversion can't quietly
spread.

Validated against real data: our scoring reorders 4for4's own board. Their LB2
is Jordyn Brooks; ours is Cedric Gray (190.4 vs Brooks' 188.0). Myles Garrett's
14.6 sacks are worth 40.9 points correctly converted vs 20.4 if not — a
20.4-point error on one player.

**Stats present in the projection files with no scoring rule (score zero):**

- 4for4 offense: `Pass Comp`, `Pass Att`, `Rush Att`, `Pa1D`, `Ru1D`, `Rec1D`
- 4for4 IDP: `Snap %`, `QBH`
- plus `FG` — see the open question below

All `FF Pts` columns are ignored everywhere, per CLAUDE.md.

### 5. DraftState — `engine/draft_state.py`

Append-only log of `(overall_pick, team_id, player_id, source)`, state derived
purely by replay. Corrections are appended, never mutated. Core only — no UI,
no manual-entry plumbing, as instructed.

Reconciliation: **manual > keeper > espn_sync**, and within one source the
later event corrects the earlier. `manual > espn_sync` is CLAUDE.md invariant
#4. The other two orderings are mine and are flagged below for your call.

Integrity problems (a player resolved into two slots) are *reported* via
`conflicts()`, never raised — a bad ESPN feed must not take the board down
mid-draft.

### 6. Housekeeping

`.gitignore` had `data/` twice and a comment claiming the rest of `data/` was
tracked, which was false. Note this needed `data/*` rather than `data/`: git
cannot re-include a file whose parent *directory* is excluded, so the
`!data/crosswalk.csv` exception would have silently done nothing. Verified with
`git check-ignore`. `data/crosswalk.csv` is now committed (1979 rows).

README's `Currently at: **1**` replaced with a per-priority status list.

---

### Decisions I made that you should confirm or overrule

1. **DraftState precedence `manual > keeper > espn_sync`.** Your instruction
   specified only manual-beats-espn_sync. I put keeper above espn_sync because
   CLAUDE.md says `league-config.toml` is authoritative for keepers and ESPN's
   nine `reservedForKeeper` slots disagree with it; and manual above keeper so
   a live correction doesn't require a config edit mid-draft. Both are one-line
   changes in `SOURCE_PRECEDENCE`.

2. **Defensive position aliases** (`NT`, `MLB/ILB/OLB`, `FS/SS`, `PK`) accepted
   though absent from current data. Fail-soft on unambiguous variants; `EDGE`
   and `D/ST` still raise.

3. **`data/crosswalk_review.csv` is NOT committed** — I followed your
   instruction literally (only `crosswalk.csv`). But your stated rationale
   ("hand-review effort and public player names") applies to the review file
   too, and that's the file your review effort actually lands in. Say the word
   and I'll add a second exception.

4. **4for4's `DefTD`** is a single column that doesn't itemize interception vs
   fumble return TDs. Our config scores `misc.interception_return_td` and
   `misc.fumble_return_td` separately but **both at 6.0**, so I mapped `DefTD`
   to the former. Numerically identical either way; if the values ever diverge,
   this mapping needs revisiting.

### Blocked / needs your judgment

**Kicker scoring is unresolved.** `[scoring.kicker] fgy = 0.1` is commented
"Points for each FG made", but `FGY` is ESPN's field-goal-*yardage* stat, and
those mean very different things:

- per FG made: a 38-FG kicker scores 3.8 points from field goals
- per FG yard: the same kicker scores roughly 150

The second is the only plausible kicker total. Compounding it, **4for4 gives FG
*counts* but no FG yardage**, so if it is per-yard we cannot compute kicker
points from 4for4 at all without a distance distribution — and
`fg_0_39 / fg_40_49 / fg_50_plus` are all `0.0`, so the range buckets can't
substitute.

I did **not** guess. The rule is wired as `field_goal_yards → kicker.fgy`, so
`FG` currently lands in the unscored list and kickers score from PATs only.
Whichever way you decide, it is one line in `engine/scoring.py`'s rule table.
K is 1 of 13 starting slots, so this does not block anything else.

**What I need from you:** confirm whether `fgy` is per-yard or per-FG-made, and
if per-yard, where FG distance data should come from (ESPN
`kona_player_info` may carry it).

---

## 2026-08-20 — Kicker scoring resolved

`fgy` confirmed as ESPN's **"FG Made Yards" (FGY)** — points per field-goal
*yard*, not per FG made. The `field_goal_yards -> kicker.fgy` rule was already
correct; the open question was only where yardage data comes from, since 4for4
supplies FG counts and no yardage.

**Answer: ESPN `kona_player_info`.** Stat ids decoded and verified across 8
kickers (the three range buckets sum exactly to FG made for every one, and
implied average FG distance lands at 38.5-38.7 yards throughout):

| stat id | meaning |
|---|---|
| **214** | **FG made yards** — this is FGY |
| 215 / 216 | FG missed yards / total FG yards attempted |
| 83 / 84 / 85 | FG made / attempted / missed |
| 86 / 87 / 88 | XP made / attempted / missed |
| 74 / 77 / 80 | FG made 50+ / 40-49 / 0-39 |

Worked example, Brandon Aubrey: 46.10 XP x 1.0 + 1371.5 FG yards x 0.1 =
**183.3 points**. Read the wrong way (per FG made) he would score 46.1 + 3.5 =
49.6 and kickers would rank on PATs alone.

**Consequence for the projection loader: kickers must be sourced from ESPN, not
4for4.** Every other position can come from 4for4; K is the exception.

Added three named kicker tests (176 total now). Corrected the misleading
`# Points for each FG made` comment in both `docs/league-config.toml` and the
`.example` — comment only, no value changed.

### Answers to the other open items

- **keeper > espn_sync confirmed.** Keeper declarations go into ESPN in ~4-5
  days (so ~2026-08-24/25). Precedence stays as implemented; once ESPN has them
  the two should agree, which turns this into a useful cross-check rather than a
  conflict. Worth adding a config-vs-ESPN keeper diff at that point.
- **`POSITION_ALIASES` in `engine/positions.py` confirmed** as correct.
- **`data/crosswalk_review.csv` is now committed** with its own `!` exception.

---

## 2026-08-20 — Experiment #1: does mDraftDetail update live? NO.

Ran `tools/poll_draft.py` against a live ESPN mock draft (league 1596648425 —
same 12 teams and same round-1 order as our real league, so a faithful clone).

Method: background poll at 3s intervals with `--dump`, ~33 snapshots captured
to `data/draft_snapshots/`, while the draft ran in the browser.

Result, with the browser at **pick 67 of 264**:

| view | picks with real playerId | rostered players |
|---|---|---|
| mDraftDetail | 0 | – |
| mRoster | 0 | 0 |
| mTeam | 0 | 0 |
| mMatchup | 0 | 0 |
| mStatus / mSettings | 0 | – |

`inProgress=True` throughout, so the API knows a draft is happening — it just
will not tell you what was picked. Re-verified after the espn_s2 cookie was
refreshed mid-experiment, ruling out stale auth.

Decision recorded in docs/decisions.md: **cut live sync**. See that entry for
consequences. Net effect on the schedule: priority #6 disappears, and the time
goes to the projection loader, VOR, and making manual entry good.

Note for the poller: it reads `.env` once at startup, so refreshing cookies
mid-run requires a restart. Fine for a throwaway, worth knowing on draft day if
we use it for post-draft reconciliation.

---

## 2026-08-21 — Seven more seasons of draft history (2016-2022)

Converted from a single xlsx (one sheet per year) into per-year CSVs matching
the 2023-25 format, via `tools/convert_historical_xlsx.py`. xlsx is a zip of
XML, so the converter uses the standard library rather than adding openpyxl
for a one-off. **2675 picks across 10 seasons now load.**

The old sheets are shaped differently: rounds are separator rows rather than a
column, `NO.` is a float string, `Player` packs name/NFL team/position as
`"Derrick Henry Ten, RB"`, and `Team` is the fantasy manager. 2020 is 300 picks
over 25 rounds (COVID), as expected.

**Punters.** 2019 round 22 spent a pick on Kaare Vedvik (P). The league used to
roster punters and dropped them for adding variance without signal. The loader
now separates "a real position we do not roster" (skipped and reported) from "a
position we do not recognise" (still raises), so a genuine typo stays loud.

### Open: no keeper flags before 2023

The 2016-2022 sheets have no keeper column, and the evidence says those drafts
DID have keepers. "Same player, same round, consecutive years" -- what a keeper
kept in its original round looks like -- fires 39-57 times per year in the old
seasons, indistinguishable from the 47-58 in seasons we know had exactly 36.

Inferring them is not good enough. Calibrated against the flagged years:

| year | real keepers | heuristic flags | recall | precision |
|---|---|---|---|---|
| 2023 | 36 | 58 | 89% | 55% |
| 2024 | 36 | 47 | 92% | 70% |
| 2025 | 36 | 52 | 92% | 63% |

Throwing out 11-22 genuine picks a year to catch 33 keepers is a bad trade.

**Therefore `DEFAULT_YEARS` is the keeper-flagged set only (2023-25).** The
older seasons load on request but print a warning, because timing priors built
on them count kept players as live picks and overstate early-round demand by
roughly 36 picks a year.

Jim is looking for the keeper information for 2016-2022. If it turns up, widen
`DEFAULT_YEARS` to `ALL_YEARS` and the priors get ~3x the data. If it does not,
those seasons stay excluded.
