"""Tests for value-over-replacement.

The two functions marked TODO in engine/vor.py are specified here. Run
`uv run pytest tests/test_vor.py` — they fail until you write them.

Our roster: 1 QB, 1 RB, 1 RB/WR, 3 WR/TE, 2 DL, 2 LB, 2 DB, 1 K, 12 teams.
Note WR has NO dedicated slot — every starting WR sits in a flex spot.
"""

import pytest

from engine.vor import (
    NON_STARTER_SLOTS,
    SLOT_ELIGIBILITY,
    ReplacementLevel,
    VorError,
    allocate_flex_slots,
    flex_slots,
    pool_from_points,
    replacement_levels,
    starter_demand,
    value_over_replacement,
)

ROSTER = {"qb": 1, "rb": 1, "rb_wr": 1, "wr_te": 3,
          "dl": 2, "lb": 2, "db": 2, "k": 1, "be": 9, "ir": 3}


def descending(n, top=300.0, step=5.0):
    """A fake position pool: n players, evenly spaced, highest first."""
    return [top - i * step for i in range(n)]


# ===========================================================================
# YOUR FUNCTION 1: starter_demand
# ===========================================================================

def test_dedicated_slots_multiply_by_team_count():
    assert starter_demand({"qb": 1, "k": 1}, 12) == {"QB": 12, "K": 12}


def test_multi_count_slots():
    """2 DL per team across 12 teams = 24 DL starters league-wide."""
    assert starter_demand({"dl": 2, "lb": 2, "db": 2}, 12) == {"DL": 24, "LB": 24, "DB": 24}


def test_flex_slots_are_excluded():
    """rb_wr and wr_te accept two positions — allocate_flex_slots handles them."""
    assert starter_demand({"rb": 1, "rb_wr": 1, "wr_te": 3}, 12) == {"RB": 12}


def test_wr_absent_because_it_has_no_dedicated_slot():
    """The key property of this league's roster."""
    assert "WR" not in starter_demand(ROSTER, 12)
    assert "TE" not in starter_demand(ROSTER, 12)


def test_bench_and_ir_are_not_starters():
    assert starter_demand({"qb": 1, "be": 9, "ir": 3}, 12) == {"QB": 12}
    assert NON_STARTER_SLOTS == {"be", "ir"}


def test_full_roster_dedicated_demand():
    assert starter_demand(ROSTER, 12) == {
        "QB": 12, "RB": 12, "DL": 24, "LB": 24, "DB": 24, "K": 12,
    }


def test_team_count_scales_it():
    assert starter_demand({"qb": 1}, 1) == {"QB": 1}
    assert starter_demand({"qb": 1}, 10) == {"QB": 10}


def test_empty_roster_gives_empty_demand():
    assert starter_demand({}, 12) == {}


# ===========================================================================
# YOUR FUNCTION 2: pool_from_points
# ===========================================================================

def test_groups_by_position():
    out = pool_from_points([("WR", 10.0), ("RB", 5.0), ("WR", 30.0)])
    assert set(out) == {"WR", "RB"}
    assert out["RB"] == [5.0]


def test_sorted_highest_first():
    out = pool_from_points([("WR", 10.0), ("WR", 30.0), ("WR", 20.0)])
    assert out["WR"] == [30.0, 20.0, 10.0]


def test_empty_input_gives_empty_pool():
    assert pool_from_points([]) == {}


def test_single_player():
    assert pool_from_points([("K", 150.0)]) == {"K": [150.0]}


def test_output_is_accepted_by_replacement_levels():
    """replacement_levels raises on an unsorted pool — this must not trip it."""
    rows = [("QB", 100.0), ("QB", 300.0), ("QB", 200.0)]
    replacement_levels(pool_from_points(rows), {"qb": 1}, 3)  # must not raise


# ===========================================================================
# already implemented — these pass now
# ===========================================================================

def test_flex_slots_identifies_the_two_flex_spots():
    assert flex_slots(ROSTER) == {"rb_wr": 1, "wr_te": 3}


def test_slot_eligibility_matches_the_league():
    assert SLOT_ELIGIBILITY["rb_wr"] == ("RB", "WR")
    assert SLOT_ELIGIBILITY["wr_te"] == ("WR", "TE")
    assert SLOT_ELIGIBILITY["qb"] == ("QB",)


def test_flex_goes_to_the_best_available_player():
    """One flex slot, one team. WR40 beats RB40, so the slot goes to a WR."""
    pool = {"RB": [10.0, 9.0], "WR": [100.0, 99.0]}
    demand = allocate_flex_slots({"RB": 1}, {"rb_wr": 1}, pool, num_teams=1)
    assert demand["WR"] == 1
    assert demand["RB"] == 1, "dedicated demand is untouched"


def test_flex_allocation_respects_dedicated_draw_down():
    """RB's top 2 are already spoken for, so the flex compares RB3 to WR1."""
    pool = {"RB": [100.0, 99.0, 1.0], "WR": [50.0]}
    demand = allocate_flex_slots({"RB": 2}, {"rb_wr": 1}, pool, num_teams=1)
    assert demand["WR"] == 1 and demand["RB"] == 2


def test_flex_stops_when_a_position_is_exhausted():
    pool = {"RB": [10.0], "WR": [9.0]}
    demand = allocate_flex_slots({}, {"rb_wr": 5}, pool, num_teams=1)
    assert demand.get("RB", 0) + demand.get("WR", 0) == 2


def test_value_over_replacement_subtracts_the_baseline():
    levels = {"WR": ReplacementLevel("WR", 41, 80.0)}
    assert value_over_replacement(200.0, "WR", levels) == pytest.approx(120.0)


def test_value_over_replacement_can_be_negative():
    levels = {"WR": ReplacementLevel("WR", 41, 80.0)}
    assert value_over_replacement(50.0, "WR", levels) == pytest.approx(-30.0)


def test_unknown_position_falls_back_to_raw_points():
    assert value_over_replacement(42.0, "P", {}) == pytest.approx(42.0)


def test_replacement_levels_rejects_an_unsorted_pool():
    with pytest.raises(VorError, match="sorted"):
        replacement_levels({"QB": [1.0, 2.0, 3.0]}, {"qb": 1}, 12)


def test_replacement_levels_rejects_zero_teams():
    with pytest.raises(VorError, match="num_teams"):
        replacement_levels({}, {"qb": 1}, 0)


def test_qb_baseline_is_the_twelfth_qb():
    """1 QB slot x 12 teams -> QB12 is the last starter, per CLAUDE.md."""
    pool = {"QB": descending(30)}
    levels = replacement_levels(pool, {"qb": 1}, 12)
    assert levels["QB"].rank == 12
    assert levels["QB"].points == pytest.approx(pool["QB"][11])


def test_idp_baselines_are_24_deep():
    pool = {"DL": descending(60), "LB": descending(60), "DB": descending(60)}
    levels = replacement_levels(pool, {"dl": 2, "lb": 2, "db": 2}, 12)
    assert all(levels[p].rank == 24 for p in ("DL", "LB", "DB"))


def test_short_pool_clamps_to_what_exists():
    levels = replacement_levels({"K": descending(5)}, {"k": 1}, 12)
    assert levels["K"].rank == 5


def test_position_with_no_players_is_omitted():
    levels = replacement_levels({"QB": []}, {"qb": 1}, 12)
    assert "QB" not in levels
