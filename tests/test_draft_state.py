"""Tests for the append-only DraftState event log and replay-derived state.

The reconciliation rule is the part that matters live: manual > keeper >
espn_sync, and within one source the later event corrects the earlier.
"""

import pytest

from engine.draft_state import (
    ESPN_SYNC,
    KEEPER,
    MANUAL,
    DraftState,
    DraftStateError,
    PickEvent,
    keeper_events,
)


@pytest.fixture
def state() -> DraftState:
    return DraftState()


# --- event validation ------------------------------------------------------

def test_unknown_source_is_rejected():
    with pytest.raises(DraftStateError, match="unknown source"):
        PickEvent(1, 1, 100, "telepathy")


@pytest.mark.parametrize("bad", [0, -1])
def test_non_positive_overall_pick_is_rejected(bad):
    with pytest.raises(DraftStateError, match="overall_pick"):
        PickEvent(bad, 1, 100, MANUAL)


def test_the_three_documented_sources_are_accepted():
    for src in (KEEPER, MANUAL, ESPN_SYNC):
        assert PickEvent(1, 1, 100, src).source == src


# --- append-only semantics -------------------------------------------------

def test_append_stamps_a_sequence_number(state):
    a = state.record(1, 1, 100, MANUAL)
    b = state.record(2, 2, 200, MANUAL)
    assert (a.seq, b.seq) == (0, 1)


def test_corrections_are_appended_not_mutated(state):
    state.record(1, 1, 100, MANUAL)
    state.record(1, 1, 999, MANUAL)
    assert len(state.events) == 2, "the log is append-only"
    assert state.pick_at(1).player_id == 999
    assert state.count() == 1, "but only one slot is filled"


def test_state_is_a_pure_function_of_the_log(state):
    events = [
        PickEvent(1, 1, 100, ESPN_SYNC),
        PickEvent(1, 1, 101, MANUAL),
        PickEvent(2, 2, 200, KEEPER),
    ]
    state.extend(events)
    rebuilt = DraftState()
    rebuilt.extend(PickEvent(e.overall_pick, e.team_id, e.player_id, e.source) for e in events)
    assert state.resolved() == rebuilt.resolved()


# --- reconciliation --------------------------------------------------------

def test_manual_beats_espn_sync(state):
    state.record(5, 3, 100, ESPN_SYNC)
    state.record(5, 3, 200, MANUAL)
    assert state.pick_at(5).player_id == 200
    assert state.pick_at(5).source == MANUAL


def test_manual_beats_espn_sync_regardless_of_arrival_order(state):
    """ESPN polling can land after the human typed it."""
    state.record(5, 3, 200, MANUAL)
    state.record(5, 3, 100, ESPN_SYNC)
    assert state.pick_at(5).player_id == 200


def test_keeper_beats_espn_sync(state):
    """league-config.toml is authoritative for keepers; ESPN's flags disagree."""
    state.record(19, 1, 100, ESPN_SYNC)
    state.record(19, 1, 555, KEEPER)
    assert state.pick_at(19).player_id == 555
    state2 = DraftState()
    state2.record(19, 1, 555, KEEPER)
    state2.record(19, 1, 100, ESPN_SYNC)
    assert state2.pick_at(19).player_id == 555


def test_manual_beats_keeper(state):
    """A live correction overrides a stale config keeper without a config edit."""
    state.record(19, 1, 555, KEEPER)
    state.record(19, 1, 777, MANUAL)
    assert state.pick_at(19).player_id == 777


def test_same_source_later_event_wins(state):
    state.record(7, 4, 10, ESPN_SYNC)
    state.record(7, 4, 11, ESPN_SYNC)
    assert state.pick_at(7).player_id == 11


def test_espn_sync_cannot_undo_a_manual_correction(state):
    """The failure mode this rule exists to prevent: a poll clobbering a fix."""
    state.record(9, 2, 100, ESPN_SYNC)
    state.record(9, 2, 200, MANUAL)
    for _ in range(5):  # ESPN keeps insisting
        state.record(9, 2, 100, ESPN_SYNC)
    assert state.pick_at(9).player_id == 200


def test_unrelated_picks_are_untouched_by_a_conflict(state):
    state.record(1, 1, 10, ESPN_SYNC)
    state.record(2, 2, 20, ESPN_SYNC)
    state.record(1, 1, 11, MANUAL)
    assert state.pick_at(2).player_id == 20


# --- derived views ---------------------------------------------------------

def test_picks_are_ordered_by_overall_pick(state):
    for overall in (5, 1, 3):
        state.record(overall, 1, overall * 10, MANUAL)
    assert [p.overall_pick for p in state.picks()] == [1, 3, 5]


def test_drafted_player_ids_reflect_resolution(state):
    state.record(1, 1, 100, ESPN_SYNC)
    state.record(1, 1, 200, MANUAL)
    assert state.drafted_player_ids() == {200}
    assert state.is_drafted(200)
    assert not state.is_drafted(100), "the overridden player is back on the board"


def test_roster_filters_by_team_and_keeps_draft_order(state):
    state.record(3, 7, 30, MANUAL)
    state.record(1, 7, 10, KEEPER)
    state.record(2, 8, 20, MANUAL)
    assert [e.player_id for e in state.roster(7)] == [10, 30]
    assert state.team_player_ids(8) == {20}


def test_empty_state_is_empty(state):
    assert state.picks() == []
    assert state.drafted_player_ids() == set()
    assert state.count() == 0
    assert len(state) == 0
    assert state.conflicts() == []


def test_len_and_iter_use_resolved_picks(state):
    state.record(1, 1, 10, ESPN_SYNC)
    state.record(1, 1, 11, MANUAL)
    state.record(2, 2, 20, MANUAL)
    assert len(state) == 2
    assert [e.player_id for e in state] == [11, 20]


# --- integrity reporting ---------------------------------------------------

def test_duplicate_player_is_reported_not_raised(state):
    """A bad feed must degrade to a warning, never take the board down live."""
    state.record(1, 1, 100, ESPN_SYNC)
    state.record(2, 2, 100, ESPN_SYNC)
    conflicts = state.conflicts()
    assert len(conflicts) == 1
    assert conflicts[0].kind == "duplicate_player"
    assert conflicts[0].player_id == 100
    assert conflicts[0].overall_picks == (1, 2)
    assert state.count() == 2, "state still readable"


def test_resolving_a_duplicate_clears_the_conflict(state):
    state.record(1, 1, 100, ESPN_SYNC)
    state.record(2, 2, 100, ESPN_SYNC)
    state.record(2, 2, 200, MANUAL)  # human fixes the second slot
    assert state.conflicts() == []


def test_overridden_reports_loser_and_winner(state):
    state.record(4, 1, 10, ESPN_SYNC)
    state.record(4, 1, 11, MANUAL)
    (loser, winner), = state.overridden()
    assert loser.player_id == 10 and loser.source == ESPN_SYNC
    assert winner.player_id == 11 and winner.source == MANUAL


def test_no_overrides_when_every_pick_is_unique(state):
    state.record(1, 1, 10, MANUAL)
    state.record(2, 2, 20, MANUAL)
    assert state.overridden() == []


# --- keeper seeding --------------------------------------------------------

def test_keeper_events_helper_builds_keeper_sourced_events():
    events = keeper_events([(19, 1, 555), (30, 6, 777)])
    assert all(e.source == KEEPER for e in events)
    assert [(e.overall_pick, e.team_id, e.player_id) for e in events] == [
        (19, 1, 555), (30, 6, 777)
    ]


def test_a_draft_seeded_with_keepers_then_live_picks(state):
    state.extend(keeper_events([(19, 1, 555), (30, 6, 777)]))
    state.record(1, 1, 100, MANUAL)
    state.record(2, 12, 200, ESPN_SYNC)
    assert state.count() == 4
    assert [p.overall_pick for p in state.picks()] == [1, 2, 19, 30]
    assert state.team_player_ids(1) == {100, 555}
    assert state.conflicts() == []
