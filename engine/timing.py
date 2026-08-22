"""Positional-timing priors: when does this league actually take each position?

The input VONA needs. For a candidate on the board we want P(he is still there
at my next pick), and the honest way to get it here is to ask how many players
of his position these twelve managers have historically taken in a window that
size, at that point in the draft.

Why not ADP: no source sells ADP with IDP coverage, and a national ADP would
describe a generic redraft room rather than a league that starts six IDP.
See docs/decisions.md 2026-08-19.

**Keeper picks are excluded from the priors.** A player kept in round 2 says
nothing about market timing — the slot was pre-assigned, nobody chose it over
the alternatives that day. Including them would make early rounds look busier
than they are.

Three seasons is thin. Per-year IDP counts swing hard (DE 20/26/20, DT 5/3/6),
so everything here pools across years and reports the spread rather than
pretending to a precision it does not have.

Pure functions, zero I/O (invariant #1). `sources/historical.py` does the
reading; this module takes plain `(year, overall, slot)` triples.
"""

from __future__ import annotations

import math
from bisect import bisect_right
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class TimingPick:
    """One historical pick, reduced to what the timing model needs."""

    year: int
    overall: int
    slot: str


@dataclass(frozen=True)
class WindowStats:
    """How many players of one slot went in a pick window, across seasons."""

    slot: str
    start: int  #: exclusive — picks strictly after this
    end: int  #: inclusive
    per_year: Mapping[int, int]

    @property
    def mean(self) -> float:
        return sum(self.per_year.values()) / len(self.per_year) if self.per_year else 0.0

    @property
    def spread(self) -> tuple[int, int]:
        """(min, max) across seasons — the honest measure of how thin this is."""
        values = list(self.per_year.values())
        return (min(values), max(values)) if values else (0, 0)

    @property
    def stdev(self) -> float:
        values = list(self.per_year.values())
        if len(values) < 2:
            return 0.0
        mean = self.mean
        return math.sqrt(sum((v - mean) ** 2 for v in values) / (len(values) - 1))


@dataclass(frozen=True)
class TimingIndex:
    """Picks indexed for repeated window queries.

    A window count is "how many picks of slot S in season Y fall in
    (start, end]". Scanning the whole history to answer that is O(picks) per
    question, and the caller asks one question per slot per window — 8 slots x
    18 windows for a full board, so 144 scans of the same data.

    Instead, bucket the overalls by (slot, season) once and keep each bucket
    sorted. A count is then two binary searches: O(log n), no scan.
    """

    seasons: tuple[int, ...]
    slots: tuple[str, ...]
    #: (slot, season) -> sorted overall pick numbers
    _overalls: Mapping[tuple[str, int], tuple[int, ...]]

    @classmethod
    def build(cls, picks: Iterable[TimingPick]) -> TimingIndex:
        buckets: dict[tuple[str, int], list[int]] = {}
        seasons: set[int] = set()
        slots: set[str] = set()
        for pick in picks:
            buckets.setdefault((pick.slot, pick.year), []).append(pick.overall)
            seasons.add(pick.year)
            slots.add(pick.slot)
        return cls(
            seasons=tuple(sorted(seasons)),
            slots=tuple(sorted(slots)),
            _overalls={k: tuple(sorted(v)) for k, v in buckets.items()},
        )

    def count(self, slot: str, season: int, start: int, end: int) -> int:
        """Picks of `slot` in `season` falling in (start, end]."""
        overalls = self._overalls.get((slot, season))
        if not overalls:
            return 0
        return bisect_right(overalls, end) - bisect_right(overalls, start)


def as_index(picks: Sequence[TimingPick] | TimingIndex) -> TimingIndex:
    """Accept either raw picks or a prebuilt index.

    Callers in a loop should build the index ONCE and pass it in; passing raw
    picks rebuilds it every call, which is the whole cost this avoids.
    """
    return picks if isinstance(picks, TimingIndex) else TimingIndex.build(picks)


def from_history(picks: Iterable, include_keepers: bool = False) -> list[TimingPick]:
    """Reduce `HistoricalPick`s to the triples this module works on.

    Keepers are dropped by default: a kept player occupies a slot but was never
    chosen over the alternatives, so counting him inflates early-round demand.
    """
    return [
        TimingPick(year=p.year, overall=p.overall, slot=p.slot)
        for p in picks
        if include_keepers or not p.is_keeper
    ]


def seasons(picks: Sequence[TimingPick] | TimingIndex) -> list[int]:
    return list(as_index(picks).seasons)


def survival_probability(stats: WindowStats, positional_rank: int) -> float:
    """P(the `positional_rank`-th best player at this slot is still there).

    If `positional_rank` players at this slot are better than the one we are
    considering... no: `positional_rank` is 1-based, so the candidate survives
    exactly when FEWER than `positional_rank` players at his slot are taken in
    the window. With a mean rate `lam`, model the count as Poisson and return
    P(X < positional_rank).

    Poisson is the standard choice for "how many arrivals in a window" and it
    degrades gracefully at the tails, which matters because three seasons
    cannot support anything fancier. It is a modelling assumption, not a
    measurement — the raw per-season counts are on `stats.per_year`.
    """
    if positional_rank < 1:
        raise ValueError(f"positional_rank is 1-based, got {positional_rank}")
    lam = stats.mean
    if lam <= 0:
        return 1.0
    # P(X < k) = sum_{i=0}^{k-1} e^-lam lam^i / i!
    total = 0.0
    term = math.exp(-lam)
    for i in range(positional_rank):
        if i:
            term *= lam / i
        total += term
    return min(1.0, total)


def picks_in_window(
    picks: Sequence[TimingPick] | TimingIndex, slot: str, start: int, end: int
) -> dict[int, int]:
    """Count picks of one slot per season, in the window (start, end].

    `start` is EXCLUSIVE and `end` INCLUSIVE, because callers ask "between my
    pick at 30 and my next at 54" and pick 30 is already spent. So
    ``picks_in_window(picks, "DL", 30, 54)`` counts DL taken at overalls
    31..54.

    Every season present in `picks` must appear in the result, including ones
    where the count is ZERO — a season with no DL taken in that window is real
    evidence, and dropping it would bias the mean upward.

    Accepts a prebuilt `TimingIndex` as well as raw picks; pass one in if you
    are querying repeatedly.

    >>> picks_in_window(picks, "DL", 30, 54)
    {2023: 3, 2024: 1, 2025: 0}
    """
    index = as_index(picks)
    return {season: index.count(slot, season, start, end) for season in index.seasons}


def window_stats(
    picks: Sequence[TimingPick] | TimingIndex, slot: str, start: int, end: int
) -> WindowStats:
    """Bundle `picks_in_window` into a `WindowStats`.

    Thin wrapper — build the WindowStats with the same slot/start/end you were
    given and the per-season counts from `picks_in_window`.
    """
    return WindowStats(
        slot=slot, start=start, end=end,
        per_year=picks_in_window(as_index(picks), slot, start, end),
    )


def expected_taken(
    picks: Sequence[TimingPick] | TimingIndex, slot: str, start: int, end: int
) -> float:
    """Mean number of `slot` players taken in (start, end] across seasons."""
    return window_stats(picks, slot, start, end).mean


def positional_demand(
    picks: Sequence[TimingPick] | TimingIndex, start: int, end: int
) -> dict[str, float]:
    """Expected picks per slot in a window — which positions are running.

    Builds the index once and reuses it across slots. Passing raw picks here
    used to rebuild the whole scan per slot.
    """
    index = as_index(picks)
    return {slot: expected_taken(index, slot, start, end) for slot in index.slots}
