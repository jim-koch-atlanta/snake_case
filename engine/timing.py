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


def seasons(picks: Sequence[TimingPick]) -> list[int]:
    return sorted({p.year for p in picks})


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


# ---------------------------------------------------------------------------
# TODO(jim): two functions left for you — see tests/test_timing.py
# ---------------------------------------------------------------------------

def picks_in_window(
    picks: Sequence[TimingPick], slot: str, start: int, end: int
) -> dict[int, int]:
    """Count picks of one slot per season, in the window (start, end].

    `start` is EXCLUSIVE and `end` INCLUSIVE, because callers ask "between my
    pick at 30 and my next at 54" and pick 30 is already spent. So
    ``picks_in_window(picks, "DL", 30, 54)`` counts DL taken at overalls
    31..54.

    Every season present in `picks` must appear in the result, including ones
    where the count is ZERO — a season with no DL taken in that window is real
    evidence, and dropping it would bias the mean upward.

    >>> picks_in_window(picks, "DL", 30, 54)
    {2023: 3, 2024: 1, 2025: 0}
    """
    raise NotImplementedError("see tests/test_timing.py")


def window_stats(
    picks: Sequence[TimingPick], slot: str, start: int, end: int
) -> WindowStats:
    """Bundle `picks_in_window` into a `WindowStats`.

    Thin wrapper — build the WindowStats with the same slot/start/end you were
    given and the per-season counts from `picks_in_window`.
    """
    raise NotImplementedError("see tests/test_timing.py")


def expected_taken(
    picks: Sequence[TimingPick], slot: str, start: int, end: int
) -> float:
    """Mean number of `slot` players taken in (start, end] across seasons."""
    return window_stats(picks, slot, start, end).mean


def positional_demand(
    picks: Sequence[TimingPick], start: int, end: int
) -> dict[str, float]:
    """Expected picks per slot in a window — which positions are running."""
    return {
        slot: expected_taken(picks, slot, start, end)
        for slot in sorted({p.slot for p in picks})
    }
