"""Tests for the NFL position -> roster slot mapping.

Covers every position string actually present in our data files, including the
offensive positions that appear in data/historical/ but never in the
FantasyPros IDP data (QB/RB/WR/TE/K).
"""

import pytest

from engine.positions import (
    ALL_SLOTS,
    IDP_SLOTS,
    OFFENSE_SLOTS,
    UnknownPositionError,
    is_idp,
    is_known_position,
    normalize_position,
    slot_for_position,
)

# --- positions present in every source -------------------------------------

@pytest.mark.parametrize(
    "position,expected",
    [
        ("DE", "DL"),
        ("DT", "DL"),
        ("LB", "LB"),
        ("CB", "DB"),
        ("S", "DB"),
    ],
)
def test_idp_positions_map_to_idp_slots(position, expected):
    assert slot_for_position(position) == expected


@pytest.mark.parametrize(
    "position,expected",
    [
        # these appear in data/historical/ but NOT in the FantasyPros IDP data
        ("QB", "QB"),
        ("RB", "RB"),
        ("WR", "WR"),
        ("TE", "TE"),
        ("K", "K"),
    ],
)
def test_offense_positions_from_historical_data(position, expected):
    assert slot_for_position(position) == expected


def test_every_observed_position_is_known():
    # exact vocabulary observed on 2026-08-19 across historical, 4for4, fantasypros
    observed = ["WR", "RB", "TE", "QB", "K", "LB", "DE", "S", "DT", "CB"]
    assert all(is_known_position(p) for p in observed)
    assert {slot_for_position(p) for p in observed} == ALL_SLOTS


# --- normalization ---------------------------------------------------------

@pytest.mark.parametrize("raw", ["de", " DE ", "De", "\tde\n"])
def test_case_and_whitespace_insensitive(raw):
    assert slot_for_position(raw) == "DL"


def test_normalize_position_strips_slashes_and_dots():
    assert normalize_position(" d/st ") == "DST"
    assert normalize_position("S.") == "S"


# --- unknown positions fail loudly -----------------------------------------

def test_unknown_position_raises_with_known_list():
    with pytest.raises(UnknownPositionError, match="known:"):
        slot_for_position("XYZ")


def test_empty_position_raises():
    with pytest.raises(UnknownPositionError):
        slot_for_position("")
    with pytest.raises(UnknownPositionError):
        slot_for_position("   ")


@pytest.mark.parametrize("position", ["DST", "D/ST", "EDGE", "FB", "OL"])
def test_unsupported_positions_raise(position):
    # this league has no D/ST slot, and EDGE is scheme-dependent (DL or LB) —
    # both must fail loudly rather than be guessed at
    with pytest.raises(UnknownPositionError):
        slot_for_position(position)


def test_is_known_position_does_not_raise():
    assert is_known_position("LB") is True
    assert is_known_position("EDGE") is False


# --- defensive aliases -----------------------------------------------------

@pytest.mark.parametrize(
    "position,expected",
    [("NT", "DL"), ("MLB", "LB"), ("ILB", "LB"), ("OLB", "LB"),
     ("FS", "DB"), ("SS", "DB"), ("PK", "K")],
)
def test_unambiguous_aliases(position, expected):
    assert slot_for_position(position) == expected


def test_slot_names_are_accepted_as_input():
    # idempotent: feeding a slot back in returns itself
    for slot in ("DL", "LB", "DB", "QB", "RB", "WR", "TE", "K"):
        assert slot_for_position(slot) == slot


# --- slot groupings --------------------------------------------------------

def test_idp_classification():
    assert is_idp("DE") and is_idp("LB") and is_idp("CB")
    assert not is_idp("WR")
    assert not is_idp("K")  # kicker is neither offense-skill nor IDP


def test_slot_sets_are_disjoint_and_complete():
    assert OFFENSE_SLOTS & IDP_SLOTS == frozenset()
    assert OFFENSE_SLOTS | IDP_SLOTS | {"K"} == ALL_SLOTS
