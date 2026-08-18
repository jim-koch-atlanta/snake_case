"""Hand-computed tests for the pick-schedule generator.

Per CLAUDE.md: write the test first with a hand-computed tiny example
(4 teams, 3 rounds). The 4x3 snake grid, worked by hand, is:

    R1 (fwd):  overall 1,2,3,4  -> teams 1,2,3,4
    R2 (rev):  overall 5,6,7,8  -> teams 4,3,2,1
    R3 (fwd):  overall 9,10,11,12 -> teams 1,2,3,4
"""

import itertools

import pytest

from engine.schedule import (
    Keeper,
    build_pick_schedule,
    live_picks,
    next_live_pick_after,
    resolve_keeper_rounds,
    team_at_slot,
    team_live_picks,
)


def test_snake_order_4x3_no_keepers():
    sched = build_pick_schedule([1, 2, 3, 4], 3, [])
    assert len(sched) == 12
    assert {p.overall: p.team_id for p in sched} == {
        1: 1, 2: 2, 3: 3, 4: 4,
        5: 4, 6: 3, 7: 2, 8: 1,
        9: 1, 10: 2, 11: 3, 12: 4,
    }
    assert all(p.kind == "live" for p in sched)
    for t in (1, 2, 3, 4):
        assert len(team_live_picks(sched, t)) == 3


def test_team_at_slot_snake():
    order = [10, 20, 30, 40]
    assert team_at_slot(order, 1, 1) == 10  # R1 first
    assert team_at_slot(order, 2, 1) == 40  # R2 turns: last team picks first
    assert team_at_slot(order, 2, 4) == 10  # R2 last pick wraps back to first team
    assert team_at_slot(order, 3, 1) == 10  # R3 forward again


# --- collision resolution ---------------------------------------------------

def test_resolve_no_collision_is_identity():
    assert resolve_keeper_rounds([3, 7, 1], 22) == [3, 7, 1]


def test_resolve_two_same_round_backfills_one_earlier():
    # first keeper keeps X, second backfills X-1
    assert resolve_keeper_rounds([3, 3], 22) == [3, 2]


def test_resolve_three_same_round_cascades():
    assert resolve_keeper_rounds([5, 5, 5], 22) == [5, 4, 3]


def test_resolve_secondary_collision_cascades():
    # two want round 3 -> 3,2 ; the keeper declared in round 2 is pushed to 1
    assert resolve_keeper_rounds([3, 3, 2], 22) == [3, 2, 1]


def test_resolve_round_set_is_order_independent():
    for perm in itertools.permutations([3, 3, 2]):
        assert sorted(resolve_keeper_rounds(list(perm), 22)) == [1, 2, 3]


def test_resolve_underflow_below_round_1_raises():
    with pytest.raises(ValueError):
        resolve_keeper_rounds([1, 1], 22)
    with pytest.raises(ValueError):
        resolve_keeper_rounds([2, 2, 2], 22)


def test_resolve_out_of_range_raises():
    with pytest.raises(ValueError):
        resolve_keeper_rounds([23], 22)
    with pytest.raises(ValueError):
        resolve_keeper_rounds([0], 22)


# --- schedule with keepers --------------------------------------------------

def test_keeper_collision_marks_two_slots_4x3():
    # Team 2 keeps two players both declared round 3 -> slots at rounds 3 and 2.
    # Team 2's slots: R1p2 -> overall 2 (live); R2p3 -> overall 7; R3p2 -> overall 10.
    keepers = [Keeper(2, "A", 3), Keeper(2, "B", 3)]
    by_overall = {p.overall: p for p in build_pick_schedule([1, 2, 3, 4], 3, keepers)}

    assert by_overall[2].kind == "live"

    assert by_overall[7].kind == "keeper"
    assert by_overall[7].round == 2
    assert by_overall[7].declared_round == 3
    assert by_overall[7].player == "B"  # second keeper backfills the earlier round

    assert by_overall[10].kind == "keeper"
    assert by_overall[10].round == 3
    assert by_overall[10].player == "A"  # first keeper holds the declared round

    sched = list(by_overall.values())
    assert len(team_live_picks(sched, 2)) == 1  # only round-1 slot survives
    for t in (1, 3, 4):
        assert len(team_live_picks(sched, t)) == 3  # untouched


def test_unknown_keeper_team_raises():
    with pytest.raises(ValueError):
        build_pick_schedule([1, 2], 3, [Keeper(99, "X", 1)])


def test_duplicate_draft_order_raises():
    with pytest.raises(ValueError):
        build_pick_schedule([1, 1], 3, [])


# --- next-live-pick helper --------------------------------------------------

def test_next_live_pick_after():
    sched = build_pick_schedule([1, 2, 3, 4], 3, [])  # team 1 at overall 1, 8, 9
    assert next_live_pick_after(sched, 1, 1).overall == 8
    assert next_live_pick_after(sched, 8, 1).overall == 9
    assert next_live_pick_after(sched, 9, 1) is None


# --- full-size sanity (12 teams, 22 rounds) ---------------------------------

def test_full_12x22_counts():
    order = list(range(1, 13))
    sched = build_pick_schedule(order, 22, [])
    assert len(sched) == 264
    assert len(live_picks(sched)) == 264
    r2p1 = next(p for p in sched if p.round == 2 and p.pick_in_round == 1)
    assert r2p1.team_id == 12  # snake turn
    for t in order:
        assert len(team_live_picks(sched, t)) == 22


def test_full_12x22_with_36_keepers_gives_228_live():
    # matches league facts: 3 keepers/team, 36 pre-assigned, 228 live picks.
    order = list(range(1, 13))
    keepers = [Keeper(t, f"P{t}-{r}", r) for t in order for r in (1, 5, 10)]
    sched = build_pick_schedule(order, 22, keepers)
    assert len(sched) == 264
    assert sum(1 for p in sched if p.kind == "keeper") == 36
    assert len(live_picks(sched)) == 228
    for t in order:
        assert len(team_live_picks(sched, t)) == 19
