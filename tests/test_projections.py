"""Tests for the projection loader.

The two functions marked TODO in sources/projections.py are specified here.
Run `uv run pytest tests/test_projections.py` — they fail until you write them,
then go green. Everything else in this file already passes.
"""

import pytest

from sources.projections import (
    IDP_COLUMNS,
    METADATA_COLUMNS,
    OFFENSE_COLUMNS,
    ValuedPlayer,
    espn_season_stats,
    stat_line_from_row,
    top_by_slot,
    unmapped_columns,
)


def player(name, slot, points, espn_id=1):
    return ValuedPlayer(espn_id=espn_id, name=name, slot=slot, position=slot,
                        team="XX", points=points, stats={}, source="test")


# ===========================================================================
# YOUR FUNCTION 1: stat_line_from_row
# ===========================================================================

def test_renames_columns_to_canonical_stat_names():
    row = {"Tackles": "38.1", "Assists": "12.7", "Sacks": "14.6"}
    assert stat_line_from_row(row, IDP_COLUMNS) == {
        "solo_tackles": 38.1, "assisted_tackles": 12.7, "sacks": 14.6,
    }


def test_values_become_floats_not_strings():
    out = stat_line_from_row({"Tackles": "38.1"}, IDP_COLUMNS)
    assert out["solo_tackles"] == 38.1
    assert isinstance(out["solo_tackles"], float)


def test_blank_cells_are_zero_not_an_exception():
    """float("") raises ValueError. Empty cells are common in these exports."""
    out = stat_line_from_row({"Tackles": "38.1", "Assists": ""}, IDP_COLUMNS)
    assert out["assisted_tackles"] == 0.0


def test_columns_missing_from_the_row_are_skipped():
    """Not every column in the map appears in every file."""
    out = stat_line_from_row({"Tackles": "10"}, IDP_COLUMNS)
    assert out == {"solo_tackles": 10.0}
    assert "sacks" not in out


def test_columns_not_in_the_map_are_ignored():
    """QBH and Snap % are real stats this league does not score."""
    row = {"Tackles": "10", "QBH": "23.7", "Snap %": "78", "FF Pts": "302.9"}
    out = stat_line_from_row(row, IDP_COLUMNS)
    assert out == {"solo_tackles": 10.0}
    assert "FF Pts" not in out and "QBH" not in out


def test_works_for_offense_columns_too():
    row = {"Rec": "100", "Rec Yds": "1200", "Rec TD": "8", "Pass Att": "0"}
    assert stat_line_from_row(row, OFFENSE_COLUMNS) == {
        "receptions": 100.0, "receiving_yards": 1200.0, "receiving_td": 8.0,
    }


def test_empty_row_gives_an_empty_stat_line():
    assert stat_line_from_row({}, IDP_COLUMNS) == {}


def test_sacks_are_passed_through_whole_not_doubled():
    """engine/scoring.py applies the half-sack conversion. Do not pre-multiply."""
    assert stat_line_from_row({"Sacks": "12"}, IDP_COLUMNS)["sacks"] == 12.0


# ===========================================================================
# YOUR FUNCTION 2: top_by_slot
# ===========================================================================

def test_returns_highest_scoring_first():
    players = [player("low", "LB", 50.0), player("high", "LB", 200.0),
               player("mid", "LB", 120.0)]
    assert [p.name for p in top_by_slot(players, "LB")] == ["high", "mid", "low"]


def test_filters_to_the_requested_slot():
    players = [player("lb", "LB", 100.0), player("dl", "DL", 999.0)]
    assert [p.name for p in top_by_slot(players, "LB")] == ["lb"]


def test_respects_the_limit():
    players = [player(f"p{i}", "DL", float(i)) for i in range(50)]
    assert len(top_by_slot(players, "DL", limit=24)) == 24


def test_limit_larger_than_the_pool_is_fine():
    players = [player("only", "K", 10.0)]
    assert len(top_by_slot(players, "K", limit=20)) == 1


def test_unknown_slot_gives_an_empty_list():
    assert top_by_slot([player("x", "LB", 1.0)], "QB") == []


def test_empty_pool_gives_an_empty_list():
    assert top_by_slot([], "LB") == []


def test_last_element_is_the_replacement_baseline():
    """CLAUDE.md: replacement is DL24, so top_by_slot(..., 24)[-1] is the baseline."""
    players = [player(f"dl{i}", "DL", float(100 - i)) for i in range(40)]
    assert top_by_slot(players, "DL", limit=24)[-1].points == pytest.approx(77.0)


# ===========================================================================
# already implemented — these pass now
# ===========================================================================

def test_unmapped_columns_reports_unscored_stats():
    row = {"Player": "x", "Tackles": "1", "QBH": "2", "Snap %": "3"}
    assert unmapped_columns(row, IDP_COLUMNS) == {"QBH", "Snap %"}


def test_unmapped_columns_ignores_metadata_and_ff_pts():
    row = {k: "1" for k in METADATA_COLUMNS}
    assert unmapped_columns(row, IDP_COLUMNS) == set()


def test_espn_season_stats_picks_the_season_block_not_per_game():
    p = {"stats": [
        {"statSourceId": 1, "seasonId": 2026, "statSplitTypeId": 1, "stats": {"214": "79.6"}},
        {"statSourceId": 1, "seasonId": 2026, "statSplitTypeId": 0, "stats": {"214": "1371.5"}},
        {"statSourceId": 0, "seasonId": 2025, "statSplitTypeId": 0, "stats": {"214": "999"}},
    ]}
    assert espn_season_stats(p)["214"] == pytest.approx(1371.5)


def test_espn_season_stats_returns_empty_when_unprojected():
    assert espn_season_stats({"stats": []}) == {}
    assert espn_season_stats({}) == {}
