"""Tests for folding hand-reviewed resolutions back into the crosswalk.

The merge is where a human's decisions become the thing the draft board reads,
so the failure modes that matter are: losing a resolution, silently keeping a
stale automatic match, and accepting a half-finished review as complete.
"""

import pytest

from sources.merge_crosswalk import (
    HAND_REVIEWED,
    MergeError,
    classify_resolution,
    merge,
)


def auto(source="4for4_idp", key="k1", name="Fred Warner", espn_id="1", slot="LB"):
    return {
        "source": source, "source_key": key, "source_name": name,
        "source_team": "SF", "source_pos": "LB", "slot": slot,
        "espn_id": espn_id, "espn_name": name, "espn_pos": "LB",
        "match_type": "exact name, slot agrees", "score": "1.000",
    }


def review(source="4for4_idp", key="k2", name="Lamar Jackson", resolved="",
           slot="QB", cands=()):
    row = {
        "source": source, "source_key": key, "source_name": name,
        "source_team": "BAL", "source_pos": "QB", "slot": slot,
        "verdict": "review", "reason": "ambiguous", "resolved_espn_id": resolved,
    }
    for i in (1, 2, 3):
        eid, nm, pos = cands[i - 1] if i <= len(cands) else ("", "", "")
        row[f"cand{i}_espn_id"] = eid
        row[f"cand{i}_name"] = nm
        row[f"cand{i}_pos"] = pos
        row[f"cand{i}_score"] = "1.000" if eid else ""
    return row


SPINE = {
    "1": {"id": 1, "fullName": "Fred Warner", "defaultPositionId": 11},
    "3916387": {"id": 3916387, "fullName": "Lamar Jackson", "defaultPositionId": 1},
    "4034849": {"id": 4034849, "fullName": "Lamar Jackson", "defaultPositionId": 12},
    "99": {"id": 99, "fullName": "Micah Parsons", "defaultPositionId": 10},
}


# --- resolution parsing ----------------------------------------------------

@pytest.mark.parametrize("raw", ["", "   ", None])
def test_blank_is_unreviewed(raw):
    assert classify_resolution(raw)[0] == "blank"


@pytest.mark.parametrize("raw", ["none", "NONE", "No Match", "n/a", "-", "x"])
def test_sentinels_mean_no_espn_player_exists(raw):
    assert classify_resolution(raw)[0] == "no_match"


def test_digits_are_an_espn_id():
    assert classify_resolution(" 4362628 ") == ("espn_id", "4362628")


@pytest.mark.parametrize("raw", ["4362628?", "Ja'Marr Chase", "4362628, 123"])
def test_anything_else_is_invalid(raw):
    assert classify_resolution(raw)[0] == "invalid"


# --- merging ---------------------------------------------------------------

def test_resolved_row_is_added_and_stamped_hand_reviewed():
    rows, rep = merge([auto()], [review(resolved="3916387")], SPINE)
    added = next(r for r in rows if r["source_key"] == "k2")
    assert added["espn_id"] == "3916387"
    assert added["match_type"] == HAND_REVIEWED
    assert added["espn_name"] == "Lamar Jackson"
    assert rep["merged"] == 1


def test_automatic_matches_are_preserved():
    rows, _ = merge([auto()], [review(resolved="3916387")], SPINE)
    assert any(r["match_type"] == "exact name, slot agrees" for r in rows)
    assert len(rows) == 2


def test_a_review_row_supersedes_an_auto_row_with_the_same_key():
    """If the matcher guessed and a human later disagreed, the human wins."""
    rows, rep = merge(
        [auto(key="dup", espn_id="1")],
        [review(key="dup", resolved="99")],
        SPINE,
    )
    assert len(rows) == 1
    assert rows[0]["espn_id"] == "99"
    assert rows[0]["match_type"] == HAND_REVIEWED
    assert rep["replaced"] == 1


def test_no_match_sentinel_excludes_the_player():
    rows, rep = merge([auto()], [review(resolved="none")], SPINE)
    assert rep["no_match"] == 1
    assert all(r["source_key"] != "k2" for r in rows)


def test_no_match_removes_a_previously_automatic_row():
    rows, rep = merge([auto(key="dup")], [review(key="dup", resolved="none")], SPINE)
    assert rows == []
    assert rep["no_match"] == 1


def test_blank_rows_block_the_merge_by_default():
    with pytest.raises(MergeError, match="blank resolved_espn_id"):
        merge([auto()], [review(resolved="")], SPINE)


def test_allow_partial_merges_what_is_done():
    rows, rep = merge(
        [auto()],
        [review(key="a", resolved="3916387"), review(key="b", resolved="")],
        SPINE, allow_partial=True,
    )
    assert rep["merged"] == 1 and rep["blank"] == 1
    assert all(r["source_key"] != "b" for r in rows)


def test_invalid_resolution_is_a_hard_error():
    with pytest.raises(MergeError, match="must be a number"):
        merge([auto()], [review(resolved="Lamar Jackson")], SPINE)


# --- reporting -------------------------------------------------------------

def test_id_outside_the_cached_spine_is_reported_not_rejected():
    """Deep-roster players looked up by hand legitimately fall outside the cache."""
    rows, rep = merge(
        [], [review(key="ad", name="Aaron Donald", resolved="16716",
                    cands=(("16716", "Aaron Donald", "DT"),))],
        SPINE,
    )
    assert rep["off_spine"] == [("Aaron Donald", "16716")]
    assert len(rows) == 1, "still merged"
    assert rows[0]["espn_name"] == "Aaron Donald", "name recovered from candidates"


def test_slot_difference_is_reported():
    """Edge rushers: 4for4 says LB, ESPN says DE -> DL. ESPN governs the slot."""
    _, rep = merge(
        [], [review(key="mp", name="Micah Parsons", resolved="99", slot="LB")], SPINE
    )
    assert rep["slot_differs"] == [("Micah Parsons", "LB", "DL", "DE")]


def test_espn_position_is_taken_from_the_spine_not_the_source():
    rows, _ = merge(
        [], [review(key="mp", name="Micah Parsons", resolved="99", slot="LB")], SPINE
    )
    assert rows[0]["espn_pos"] == "DE"
    assert rows[0]["slot"] == "LB", "the source's own slot is preserved for reference"


def test_duplicate_espn_id_within_a_source_is_reported():
    """Aliases like Hollywood/Marquise Brown are legitimate but worth an eyeball."""
    _, rep = merge(
        [],
        [review(source="historical", key="a", name="Hollywood Brown", resolved="99"),
         review(source="historical", key="b", name="Marquise Brown", resolved="99")],
        SPINE,
    )
    assert len(rep["duplicate_ids"]) == 1
    src, eid, names = rep["duplicate_ids"][0]
    assert (src, eid) == ("historical", "99")
    assert names == ["Hollywood Brown", "Marquise Brown"]


def test_same_id_in_different_sources_is_not_a_conflict():
    """Every source is expected to map its own row to the same ESPN player."""
    _, rep = merge(
        [],
        [review(source="4for4_idp", key="a", name="X", resolved="99"),
         review(source="fantasypros_idp", key="a", name="X", resolved="99")],
        SPINE,
    )
    assert rep["duplicate_ids"] == []


def test_empty_review_is_a_no_op():
    rows, rep = merge([auto()], [], SPINE)
    assert len(rows) == 1
    assert rep["merged"] == 0 and rep["blank"] == 0


def test_merged_rows_have_the_crosswalk_schema():
    rows, _ = merge([auto()], [review(resolved="3916387")], SPINE)
    for r in rows:
        assert set(r) == {
            "source", "source_key", "source_name", "source_team", "source_pos",
            "slot", "espn_id", "espn_name", "espn_pos", "match_type", "score",
        }
