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
from dataclasses import dataclass
from pathlib import Path

from engine.positions import UnknownPositionError, slot_for_position

ROOT = Path(__file__).resolve().parent.parent
HISTORICAL_DIR = ROOT / "data" / "historical"
DEFAULT_YEARS = (2023, 2024, 2025)
TEAMS_PER_ROUND = 12


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


def load_year(year: int, directory: Path = HISTORICAL_DIR) -> list[HistoricalPick]:
    path = directory / f"draft-{year}.csv"
    if not path.exists():
        raise HistoricalError(f"missing historical draft file: {path}")

    picks: list[HistoricalPick] = []
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
            ))

    if not picks:
        raise HistoricalError(f"{path.name} produced no picks")
    return picks


def load_drafts(
    years: tuple[int, ...] = DEFAULT_YEARS, directory: Path = HISTORICAL_DIR
) -> list[HistoricalPick]:
    """Every pick from every available past draft, ordered by year then overall."""
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
