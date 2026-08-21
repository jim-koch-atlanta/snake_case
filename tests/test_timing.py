"""Tests for positional-timing priors.

Two functions are marked TODO in engine/timing.py. Run
`uv run pytest tests/test_timing.py`.
"""

import pytest

from engine.timing import (
    TimingPick,
    WindowStats,
    expected_taken,
    from_history,
    picks_in_window,
    positional_demand,
    seasons,
    survival_probability,
    window_stats,
)


def tp(year, overall, slot):
    return TimingPick(year=year, overall=overall, slot=slot)


#: 3 seasons. DL taken at overalls 31/40/50 in 2023, 33 in 2024, none in 2025.
POOL = [
    tp(2023, 31, "DL"), tp(2023, 40, "DL"), tp(2023, 50, "DL"), tp(2023, 5, "WR"),
    tp(2024, 33, "DL"), tp(2024, 12, "WR"), tp(2024, 60, "DL"),
    tp(2025, 8, "WR"), tp(2025, 70, "DL"),
]


# ===========================================================================
# YOUR FUNCTION 1: picks_in_window
# ===========================================================================

def test_counts_per_season():
    assert picks_in_window(POOL, "DL", 30, 54) == {2023: 3, 2024: 1, 2025: 0}


def test_a_season_with_zero_is_still_reported():
    """Dropping it would bias the mean upward — a quiet season is evidence."""
    out = picks_in_window(POOL, "DL", 30, 54)
    assert 2025 in out and out[2025] == 0


def test_start_is_exclusive_end_is_inclusive():
    """Caller asks "between my pick at 30 and my next at 54"; 30 is spent."""
    assert picks_in_window(POOL, "DL", 31, 50)[2023] == 2, "31 excluded, 50 included"
    assert picks_in_window(POOL, "DL", 30, 31)[2023] == 1, "31 included"


def test_filters_by_slot():
    assert picks_in_window(POOL, "WR", 0, 264) == {2023: 1, 2024: 1, 2025: 1}


def test_slot_with_no_picks_anywhere_is_all_zeroes():
    out = picks_in_window(POOL, "QB", 0, 264)
    assert out == {2023: 0, 2024: 0, 2025: 0}


def test_empty_window():
    assert picks_in_window(POOL, "DL", 100, 110) == {2023: 0, 2024: 0, 2025: 0}


def test_empty_pool_gives_empty_dict():
    assert picks_in_window([], "DL", 0, 264) == {}


# ===========================================================================
# YOUR FUNCTION 2: window_stats
# ===========================================================================

def test_carries_slot_and_bounds_through():
    s = window_stats(POOL, "DL", 30, 54)
    assert (s.slot, s.start, s.end) == ("DL", 30, 54)


def test_per_year_matches_picks_in_window():
    s = window_stats(POOL, "DL", 30, 54)
    assert s.per_year == picks_in_window(POOL, "DL", 30, 54)


def test_mean_spread_and_stdev_come_out():
    s = window_stats(POOL, "DL", 30, 54)
    assert s.mean == pytest.approx(4 / 3)
    assert s.spread == (0, 3)
    assert s.stdev > 0


# ===========================================================================
# already implemented — these pass now
# ===========================================================================

def test_from_history_drops_keepers_by_default():
    class H:
        def __init__(self, year, overall, slot, is_keeper):
            self.year, self.overall, self.slot, self.is_keeper = year, overall, slot, is_keeper
    rows = [H(2023, 1, "WR", False), H(2023, 2, "RB", True)]
    assert [p.slot for p in from_history(rows)] == ["WR"]
    assert len(from_history(rows, include_keepers=True)) == 2


def test_seasons_are_sorted_and_unique():
    assert seasons(POOL) == [2023, 2024, 2025]


def test_survival_falls_as_the_window_gets_busier():
    quiet = WindowStats("DL", 0, 10, {2023: 0, 2024: 0, 2025: 0})
    busy = WindowStats("DL", 0, 10, {2023: 8, 2024: 9, 2025: 10})
    assert survival_probability(quiet, 1) == pytest.approx(1.0)
    assert survival_probability(busy, 1) < 0.01


def test_survival_rises_with_positional_rank():
    """The 6th-best DL survives more often than the best one does."""
    s = WindowStats("DL", 0, 24, {2023: 3, 2024: 2, 2025: 4})
    assert survival_probability(s, 1) < survival_probability(s, 6)


def test_survival_of_a_never_taken_slot_is_certain():
    assert survival_probability(WindowStats("K", 0, 24, {2023: 0}), 1) == pytest.approx(1.0)


def test_survival_rejects_a_zero_rank():
    with pytest.raises(ValueError, match="1-based"):
        survival_probability(WindowStats("DL", 0, 24, {2023: 1}), 0)


def test_expected_taken_is_the_window_mean():
    assert expected_taken(POOL, "DL", 30, 54) == pytest.approx(4 / 3)


def test_positional_demand_covers_every_slot_seen():
    demand = positional_demand(POOL, 0, 264)
    assert set(demand) == {"DL", "WR"}
    assert demand["WR"] == pytest.approx(1.0)
