"""Tests for the draft board.
Four functions are marked TODO in engine/board.py: three small helpers and
`search_players`, which is the one you will actually be typing into under a
90-second clock. Run `uv run pytest tests/test_board.py`.
"""


from engine.board import (
    BoardPlayer,
    available_players,
    board_view,
    draft_progress,
    picks_until,
    roster_by_slot,
    search_players,
    to_board_players,
)
from engine.draft_state import MANUAL, DraftState
from engine.schedule import Keeper, build_pick_schedule
from engine.vor import ReplacementLevel


def bp(espn_id, name, slot="WR", points=100.0, vor=50.0, team="XX"):
    return BoardPlayer(espn_id=espn_id, name=name, slot=slot, team=team,
                       points=points, vor=vor)


CHASE = bp(1, "Ja'Marr Chase", "WR", 228.6, 109.7, "CIN")
NACUA = bp(2, "Puka Nacua", "WR", 225.0, 106.0, "LAR")
TAYLOR = bp(3, "Jonathan Taylor", "RB", 256.2, 88.6, "IND")
CAMPBELL = bp(4, "Jack Campbell", "LB", 200.1, 62.8, "DET")
STBROWN = bp(5, "Amon-Ra St. Brown", "WR", 197.5, 78.5, "DET")
POOL = [CHASE, NACUA, TAYLOR, STBROWN, CAMPBELL]


# ===========================================================================
# YOUR HELPER 1: available_players
# ===========================================================================

def test_removes_drafted_players():
    assert available_players(POOL, {NACUA.espn_id}) == [CHASE, TAYLOR, STBROWN, CAMPBELL]


def test_preserves_input_order():
    """to_board_players already sorted by VOR — do not re-sort."""
    assert available_players(POOL, set()) == POOL


def test_empty_drafted_set_returns_everything():
    assert len(available_players(POOL, set())) == len(POOL)


def test_all_drafted_returns_empty():
    assert available_players(POOL, {p.espn_id for p in POOL}) == []


def test_unknown_drafted_id_is_harmless():
    """A keeper or an off-pool player may be drafted but not in our projections."""
    assert available_players(POOL, {99999}) == POOL


# ===========================================================================
# YOUR HELPER 2: roster_by_slot
# ===========================================================================

def test_groups_my_picks_by_slot():
    state = DraftState()
    state.record(1, 14, CHASE.espn_id, MANUAL)
    state.record(2, 14, NACUA.espn_id, MANUAL)
    state.record(3, 14, CAMPBELL.espn_id, MANUAL)
    by_id = {p.espn_id: p for p in POOL}
    assert roster_by_slot(state.roster(14), by_id) == {
        "WR": [CHASE, NACUA], "LB": [CAMPBELL],
    }


def test_keeps_pick_order_within_a_slot():
    state = DraftState()
    state.record(1, 14, NACUA.espn_id, MANUAL)
    state.record(2, 14, CHASE.espn_id, MANUAL)
    by_id = {p.espn_id: p for p in POOL}
    assert roster_by_slot(state.roster(14), by_id)["WR"] == [NACUA, CHASE]


def test_unknown_player_id_is_skipped_not_a_crash():
    """Keepers and off-pool players are not in the projections. Must not crash."""
    state = DraftState()
    state.record(1, 14, 999999, MANUAL)
    state.record(2, 14, CHASE.espn_id, MANUAL)
    assert roster_by_slot(state.roster(14), {p.espn_id: p for p in POOL}) == {"WR": [CHASE]}


def test_empty_roster_gives_empty_dict():
    assert roster_by_slot([], {}) == {}


# ===========================================================================
# YOUR HELPER 3: picks_until
# ===========================================================================

def schedule_4x3():
    # teams 1..4, 3 rounds. Team 1 picks at overall 1, 8, 9.
    return build_pick_schedule([1, 2, 3, 4], 3, [])


def test_counts_picks_before_my_next():
    """After overall 1, team 1 next picks at 8. Picks 2-7 are in between = 6."""
    assert picks_until(schedule_4x3(), my_team_id=1, after_overall=1) == 6


def test_zero_when_i_am_next():
    """After overall 8, team 1 picks again at 9 — nobody in between."""
    assert picks_until(schedule_4x3(), my_team_id=1, after_overall=8) == 0


def test_none_when_i_have_no_picks_left():
    assert picks_until(schedule_4x3(), my_team_id=1, after_overall=9) is None


def test_from_the_start_of_the_draft():
    assert picks_until(schedule_4x3(), my_team_id=4, after_overall=0) == 3


def test_keeper_slots_do_not_count_as_picks_to_wait_for():
    """Keepers are pre-assigned; nobody waits on them."""
    sched = build_pick_schedule([1, 2, 3, 4], 3, [Keeper(2, "kept", 1)])
    # team 1 at overall 1; team 2's overall-2 slot is now a keeper, so between
    # overall 1 and team 1's next pick at 8, only 3,4,5,6,7 are live = 5
    assert picks_until(sched, my_team_id=1, after_overall=1) == 5


# ===========================================================================
# YOUR BIGGER PIECE: search_players
# ===========================================================================

def test_finds_by_surname():
    assert [p.name for p in search_players(POOL, "chase")] == ["Ja'Marr Chase"]


def test_is_case_insensitive():
    assert search_players(POOL, "CHASE")[0] is CHASE
    assert search_players(POOL, "ChAsE")[0] is CHASE


def test_matches_a_middle_word_not_just_the_start():
    assert search_players(POOL, "taylor")[0] is TAYLOR


def test_apostrophe_in_the_name_can_be_omitted():
    """Typing an apostrophe under a clock is not happening."""
    assert search_players(POOL, "jamarr")[0] is CHASE


def test_apostrophe_in_the_query_still_matches():
    assert search_players(POOL, "ja'marr")[0] is CHASE


def test_periods_and_hyphens_are_ignored_both_ways():
    assert search_players(POOL, "amonra")[0] is STBROWN
    assert search_players(POOL, "st brown")[0] is STBROWN


def test_prefix_matches_rank_above_contains_matches():
    """'ja' starts Ja'Marr but only appears mid-word in Jack — Chase first."""
    names = [p.name for p in search_players(POOL, "ja")]
    assert names[0] == "Ja'Marr Chase"
    assert "Jack Campbell" in names


def test_ties_keep_the_incoming_vor_order():
    """Two equally good matches stay in the order given (already VOR-sorted)."""
    pool = [bp(1, "Aaron Smith", vor=90.0), bp(2, "Zach Smith", vor=10.0)]
    assert [p.name for p in search_players(pool, "smith")] == ["Aaron Smith", "Zach Smith"]


def test_empty_query_returns_everything():
    assert len(search_players(POOL, "", limit=99)) == len(POOL)


def test_limit_caps_the_result():
    assert len(search_players(POOL, "", limit=2)) == 2


def test_no_match_gives_empty_list():
    assert search_players(POOL, "zzzzz") == []


# ===========================================================================
# already implemented — these pass now
# ===========================================================================

def test_to_board_players_sorts_by_vor():
    class P:
        def __init__(self, i, n, s, pts):
            self.espn_id, self.name, self.slot, self.points, self.team = i, n, s, pts, "XX"
    levels = {"WR": ReplacementLevel("WR", 37, 119.0), "QB": ReplacementLevel("QB", 12, 379.0)}
    rows = to_board_players([P(1, "qb", "QB", 444.9), P(2, "wr", "WR", 228.6)], levels)
    assert [r.name for r in rows] == ["wr", "qb"], "WR 109.7 VOR beats QB 65.9"


def test_draft_progress_detects_a_missed_pick():
    sched = schedule_4x3()
    state = DraftState()
    state.record(3, 3, 100, MANUAL)  # recorded pick 3, but 1 and 2 never entered
    p = draft_progress(sched, state, my_team_id=1)
    assert p.entered == 1 and p.elapsed == 3
    assert p.gap == 2 and not p.in_sync


def test_draft_progress_in_sync_when_every_pick_is_entered():
    sched = schedule_4x3()
    state = DraftState()
    for overall in (1, 2, 3):
        state.record(overall, overall, 100 + overall, MANUAL)
    p = draft_progress(sched, state, my_team_id=1)
    assert p.gap == 0 and p.in_sync
    assert p.on_the_clock == 4


def test_board_view_hides_drafted_players():
    state = DraftState()
    state.record(1, 1, CHASE.espn_id, MANUAL)
    assert CHASE not in board_view(POOL, state)


def test_board_view_filters_by_slot():
    assert all(p.slot == "WR" for p in board_view(POOL, DraftState(), slot="WR"))
