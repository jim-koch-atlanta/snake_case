"""Load our league's own past drafts.

`data/historical/draft-{2023,2024,2025}.csv` — 264 picks each, from this
league, by these twelve managers. This is the substitute for ADP: no source
sells ADP with IDP coverage (docs/decisions.md 2026-08-19), and even if one did
it would describe a national redraft room rather than the six people who
actually reach for linebackers here.

File shape, which is not obvious:
  - `NO.` is the pick WITHIN the round (1-12), not the overall pick. Overall is
    ``(round - 1) * 12 + NO.``; the grid is a complete 22 x 12.
  - `PLAYER` joins name and team with a NON-BREAKING SPACE; the clean name is in
    the third column, which has an EMPTY header.
  - `Keeper` is `"Yes"` or blank. 36 per year.
  - `Position` is a real NFL position (DE/DT/CB/S/LB), not a roster slot.
"""

from __future__ import annotations

import csv
import sys
from dataclasses import dataclass
from pathlib import Path

from engine.positions import UnknownPositionError, slot_for_position

ROOT = Path(__file__).resolve().parent.parent
HISTORICAL_DIR = ROOT / "data" / "historical"
#: Every season we hold data for.
ALL_YEARS = (2016, 2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025)

#: Seasons whose CSVs carry real keeper flags. Only these are trustworthy for
#: timing priors, because the model excludes keepers and an unflagged keeper
#: silently inflates early-round demand.
#:
#: 2019-2022 flags were reconstructed by hand from Jim's records and live in
#: data/historical/keepers.json. Verified: 2020 and 2022 have all 12 teams at
#: exactly 3; 2021 has 35 because one team kept only 2. Teams are NOT required
#: to keep the full three, so a year that does not total 36 is not a bug.
#:
#: 2020 ran 25 rounds (COVID). Harmless here: our windows never exceed 264
#: picks, and within that range its positional counts match every other season.
#:
#: 2018 was DELIBERATELY EXCLUDED. A 36-name list exists and the names were
#: confirmed, but by name they distribute 5/4/3x7/2/2/1 across teams, and a
#: team can keep at most three -- so the team attribution is wrong even though
#: the names may not be. That is harmless for timing priors but would silently
#: corrupt priority #7 (opponent priors), which models individual managers.
#: Data known to be wrong is worse than data that is absent. Restore it only
#: with corrected per-team attribution.
#:
#: The 2016-2018 sheets have no usable keeper flags, and the league did have
#: keepers then: "same player, same round, consecutive years" -- exactly what a keeper
#: kept in its original round looks like -- fires 39-57 times per year in those
#: seasons, indistinguishable from the 47-58 seen in years we know had 36.
#: Inferring them is not good enough: calibrated against the flagged years the
#: heuristic runs ~90% recall but only 55-70% precision.
KEEPER_FLAGGED_YEARS = (2019, 2020, 2021, 2022, 2023, 2024, 2025)

#: Safe default. Widen to ALL_YEARS once keeper flags exist for the older
#: seasons -- see docs/session-log.md 2026-08-21.
DEFAULT_YEARS = KEEPER_FLAGGED_YEARS

TEAMS_PER_ROUND = 12

#: Real NFL positions that this league does not roster. A pick at one of these
#: is a historical fact we cannot slot, so it is excluded and counted, NOT
#: treated as a data error. Anything outside this set still raises, so a
#: genuine typo stays loud.
#:
#: The league used to roster punters and dropped them for adding variance
#: without signal; 2019 round 22 spent a pick on one. Expect no P picks in
#: recent seasons.
UNROSTERABLE_POSITIONS = frozenset({"P", "PUNTER", "DST", "D/ST", "DEF", "OL", "FB"})


class HistoricalError(Exception):
    """Raised when a historical draft file cannot be read or understood."""


@dataclass(frozen=True)
class HistoricalPick:
    """One pick from a past draft."""

    year: int
    overall: int
    round: int
    pick_in_round: int
    player: str
    position: str  #: the NFL position as written in the file
    slot: str  #: our roster slot (DL/LB/DB/QB/RB/WR/TE/K)
    is_keeper: bool
    #: the fantasy manager who made the pick. Present from the reconstructed
    #: 2016-2022 files, blank in the 2023-25 exports which omit it. The input
    #: priority #7 needs to model individual drafters.
    team: str = ""


def load_year(year: int, directory: Path = HISTORICAL_DIR) -> list[HistoricalPick]:
    path = directory / f"draft-{year}.csv"
    if not path.exists():
        raise HistoricalError(f"missing historical draft file: {path}")

    picks: list[HistoricalPick] = []
    skipped: list[str] = []
    with path.open(newline="", encoding="utf-8-sig") as f:
        for line_no, row in enumerate(csv.DictReader(f), start=2):
            # the clean name lives in the unnamed third column; PLAYER joins
            # name+team with \xa0, so fall back by splitting on that
            name = (row.get("") or "").strip()
            if not name:
                name = (row.get("PLAYER") or "").replace("\xa0", " ").rsplit(" ", 1)[0].strip()
            position = (row.get("Position") or "").strip()
            if not name or not position:
                continue
            try:
                round_ = int(row["Round"])
                pick_in_round = int(row["NO."])
            except (KeyError, ValueError) as e:
                raise HistoricalError(f"{path.name} line {line_no}: bad Round/NO. — {e}") from e
            if position.strip().upper() in UNROSTERABLE_POSITIONS:
                skipped.append(f"{year} R{round_}.{pick_in_round} {name} ({position})")
                continue
            try:
                slot = slot_for_position(position)
            except UnknownPositionError as e:
                raise HistoricalError(f"{path.name} line {line_no}: {e}") from e

            picks.append(HistoricalPick(
                year=year,
                overall=(round_ - 1) * TEAMS_PER_ROUND + pick_in_round,
                round=round_,
                pick_in_round=pick_in_round,
                player=name,
                position=position,
                slot=slot,
                is_keeper=bool((row.get("Keeper") or "").strip()),
                team=(row.get("Team") or "").strip(),
            ))

    if not picks:
        raise HistoricalError(f"{path.name} produced no picks")
    if skipped:
        print(f"  note: skipped {len(skipped)} pick(s) at positions this league does "
              f"not roster: {', '.join(skipped)}", file=sys.stderr)
    return picks


def load_drafts(
    years: tuple[int, ...] = DEFAULT_YEARS, directory: Path = HISTORICAL_DIR
) -> list[HistoricalPick]:
    """Every pick from the requested past drafts, ordered by year then overall.

    Defaults to the keeper-flagged seasons only. Asking for a season without
    keeper flags is allowed but warns, because timing priors built on it will
    overstate early-round demand by roughly 36 picks a year.
    """
    unflagged = [y for y in years if y not in KEEPER_FLAGGED_YEARS]
    if unflagged:
        print(
            f"  WARNING: {unflagged} have no keeper flags. Timing priors built on "
            "them count kept players as live picks and will overstate early-round "
            "demand. See KEEPER_FLAGGED_YEARS.",
            file=sys.stderr,
        )
    picks: list[HistoricalPick] = []
    for year in years:
        picks.extend(load_year(year, directory))
    picks.sort(key=lambda p: (p.year, p.overall))
    return picks


def main() -> int:
    """Print what this league historically takes between each of my picks.

        uv run python -m sources.historical
    """
    import argparse
    import sys
    from itertools import pairwise

    from engine.schedule import build_pick_schedule, team_live_picks
    from engine.timing import from_history, positional_demand
    from sources.league_config import load_league_config

    ap = argparse.ArgumentParser(description=main.__doc__)
    ap.add_argument("--keepers", action="store_true",
                    help="include keeper picks (off by default — a kept player was never chosen)")
    args = ap.parse_args()

    try:
        picks = from_history(load_drafts(), include_keepers=args.keepers)
        cfg = load_league_config()
    except (HistoricalError, OSError, ValueError, KeyError) as e:
        print(f"ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    schedule = build_pick_schedule(cfg.draft_order, cfg.num_rounds, cfg.keepers, cfg.trades)
    mine = team_live_picks(schedule, cfg.my_team_id)
    slots = ("QB", "RB", "WR", "TE", "DL", "LB", "DB", "K")

    seasons_seen = sorted({p.year for p in picks})
    print(f"{len(picks)} picks across {seasons_seen} "
          f"({'including' if args.keepers else 'excluding'} keepers)\n")
    print("expected picks per position between each of my picks:")
    print(f"  {'window':<16} {'gap':>4}  " + "  ".join(f"{s:>5}" for s in slots))
    try:
        for a, b in pairwise(mine):
            demand = positional_demand(picks, a.overall, b.overall)
            row = "  ".join(f"{demand.get(s, 0):>5.1f}" for s in slots)
            print(f"  #{a.overall:>3} -> #{b.overall:<7} {b.overall - a.overall:>4}  {row}")
    except NotImplementedError as e:
        print(f"\n  needs the TODO functions in engine/timing.py: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
