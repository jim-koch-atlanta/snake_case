"""Value over replacement, with baselines derived from OUR lineup.

A raw projection cannot be compared across positions: Josh Allen outscores every
WR by 200 points, but so does every other startable QB, so that lead buys you
nothing. VOR subtracts a per-position *replacement level* — the quality of
player you could still get if you skipped the position entirely — and what is
left is the part that actually differentiates one draft pick from another.

Replacement level comes from slot counts, not from intuition. CLAUDE.md:

    replacement ~ RB19-20 (1 slot + flex share), WR ~ WR41 (3 WR/TE slots +
    flex share), TE competes with WR38-41, QB12, K12, DL24, LB24, DB24

Those ranks are *outputs* of this module, not inputs. Feed it the roster slots
and the player pool and it derives them — so if the league adds a flex spot,
the baselines move on their own.

The wrinkle in this league: **WR has no dedicated slot.** The roster is
1 QB / 1 RB / 1 RB-WR / 3 WR-TE / 2 DL / 2 LB / 2 DB / 1 K, so every starting
WR occupies a flex spot, and WR, RB and TE compete for the same 48 flex slots.
`allocate_flex_slots` resolves that competition greedily.

Pure functions, zero I/O (invariant #1). Deliberately decoupled from
`sources.projections.ValuedPlayer`: this module takes plain
``{slot: [points, ...]}`` so `engine/` never imports from `sources/`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

#: Roster-config slot name -> the positions eligible to fill it. Single-entry
#: tuples are dedicated slots; multi-entry tuples are flex.
SLOT_ELIGIBILITY: Mapping[str, tuple[str, ...]] = {
    "qb": ("QB",),
    "rb": ("RB",),
    "wr": ("WR",),
    "te": ("TE",),
    "k": ("K",),
    "dl": ("DL",),
    "lb": ("LB",),
    "db": ("DB",),
    "rb_wr": ("RB", "WR"),
    "wr_te": ("WR", "TE"),
}

#: Roster slots that are bench, not starters — excluded from replacement math.
NON_STARTER_SLOTS = frozenset({"be", "ir"})


class VorError(ValueError):
    """Raised when replacement levels cannot be derived."""


@dataclass(frozen=True)
class ReplacementLevel:
    """The baseline for one position."""

    position: str
    rank: int  #: 1-based; the rank of the last player drafted as a starter
    points: float


def flex_slots(roster_slots: Mapping[str, int]) -> dict[str, int]:
    """Starter slots that accept more than one position, as {slot_name: count}."""
    return {
        name: count
        for name, count in roster_slots.items()
        if name not in NON_STARTER_SLOTS
        and count
        and len(SLOT_ELIGIBILITY.get(name, ())) > 1
    }


def allocate_flex_slots(
    dedicated: Mapping[str, int],
    flex: Mapping[str, int],
    pool: Mapping[str, Sequence[float]],
    num_teams: int,
) -> dict[str, int]:
    """Distribute flex slots to positions, best-player-first.

    Starts from the dedicated demand (which positions have already been drawn
    down that far) and repeatedly hands the next flex slot to whichever eligible
    position has the strongest player still on the board. That is what managers
    actually do, and it is why WR — with no dedicated slot in this league —
    still ends up with the deepest starter demand.

    Returns total demand per position, dedicated + flex.
    """
    demand = dict(dedicated)
    remaining = {name: count * num_teams for name, count in flex.items()}

    for _ in range(sum(remaining.values())):
        best: tuple[float, str, str] | None = None
        for slot_name, left in remaining.items():
            if left <= 0:
                continue
            for position in SLOT_ELIGIBILITY[slot_name]:
                ranked = pool.get(position) or ()
                index = demand.get(position, 0)
                if index >= len(ranked):
                    continue  # position exhausted
                points = ranked[index]
                if best is None or points > best[0]:
                    best = (points, slot_name, position)
        if best is None:
            break  # every eligible position is exhausted
        _, slot_name, position = best
        demand[position] = demand.get(position, 0) + 1
        remaining[slot_name] -= 1

    return demand


def replacement_levels(
    pool: Mapping[str, Sequence[float]],
    roster_slots: Mapping[str, int],
    num_teams: int,
) -> dict[str, ReplacementLevel]:
    """Derive the replacement level for every position.

    `pool` maps a position to its points, already sorted highest first.
    The baseline is the LAST player drafted as a starter league-wide — with 12
    teams and one QB slot that is QB12, matching CLAUDE.md's convention.
    """
    if num_teams < 1:
        raise VorError(f"num_teams must be >= 1, got {num_teams}")
    for position, ranked in pool.items():
        if list(ranked) != sorted(ranked, reverse=True):
            raise VorError(f"pool[{position!r}] must be sorted highest-first")

    dedicated = starter_demand(roster_slots, num_teams)
    demand = allocate_flex_slots(dedicated, flex_slots(roster_slots), pool, num_teams)

    levels: dict[str, ReplacementLevel] = {}
    for position, count in demand.items():
        ranked = pool.get(position) or ()
        if not ranked:
            continue
        rank = min(count, len(ranked))
        if rank < 1:
            continue
        levels[position] = ReplacementLevel(
            position=position, rank=rank, points=float(ranked[rank - 1])
        )
    return levels


def value_over_replacement(points: float, position: str,
                           levels: Mapping[str, ReplacementLevel]) -> float:
    """Points above this position's replacement level.

    A position with no derived baseline (nobody projected) contributes its raw
    points rather than silently scoring zero.
    """
    level = levels.get(position)
    return points - level.points if level else points


# ---------------------------------------------------------------------------
# TODO(jim): two functions left for you — see tests/test_vor.py
# ---------------------------------------------------------------------------

def starter_demand(roster_slots: Mapping[str, int], num_teams: int) -> dict[str, int]:
    """League-wide demand from DEDICATED (single-position) starter slots.

    For every slot in `roster_slots` that is a starter slot (not in
    NON_STARTER_SLOTS) and accepts exactly ONE position, the whole league needs
    ``count * num_teams`` of that position.

    Flex slots are deliberately excluded — `allocate_flex_slots` handles those,
    because which position fills a flex spot depends on who is actually
    available. Positions with only flex slots (WR, here) must NOT appear in the
    result at all.

    Use SLOT_ELIGIBILITY to map a slot name to its positions, and
    ``len(...) == 1`` to test for dedicated.

    >>> starter_demand({"qb": 1, "rb": 1, "rb_wr": 1, "dl": 2, "be": 9}, 12)
    {'QB': 12, 'RB': 12, 'DL': 24}
    """
    result = { }
    for pos, count in roster_slots.items():
        if pos in NON_STARTER_SLOTS:
            continue
        if pos in SLOT_ELIGIBILITY and len(SLOT_ELIGIBILITY[pos]) == 1:
            result[SLOT_ELIGIBILITY[pos][0]] = count * num_teams
    return result

def pool_from_points(rows: Sequence[tuple[str, float]]) -> dict[str, list[float]]:
    """Group ``(position, points)`` pairs into the sorted pool this module wants.

    Returns ``{position: [points, ...]}`` with each list sorted HIGHEST FIRST —
    `replacement_levels` rejects an unsorted pool, since every rank lookup
    assumes that order.

    >>> pool_from_points([("WR", 10.0), ("RB", 5.0), ("WR", 30.0)])
    {'WR': [30.0, 10.0], 'RB': [5.0]}
    """
    result: dict[str, list[float]] = {}
    for position, points in rows:
        result.setdefault(position, []).append(float(points))
    for values in result.values():
        values.sort(reverse=True)
    return result
