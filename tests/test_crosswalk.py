"""Tests for crosswalk name/team normalization and match classification.

Name normalization is the load-bearing part of the crosswalk: every silent
mismatch on draft day starts here. The cases below are drawn from names that
actually appear in our data files, not invented ones.
"""

import pytest

from sources.build_crosswalk import (
    AUTO_FUZZY_FLOOR,
    EspnPlayer,
    SourceRow,
    classify,
    normalize_name,
    normalize_team,
)


def espn(espn_id, name, position, slot, team=""):
    return EspnPlayer(espn_id=espn_id, name=name, team=team, position=position,
                      slot=slot, norm=normalize_name(name))


def src(name, position, slot, team=""):
    return SourceRow(source="t", key=name, name=name, team=team,
                     position=position, slot=slot, norm=normalize_name(name))


# --- normalization ---------------------------------------------------------

def test_non_breaking_space_is_handled():
    """data/historical/ joins name+team with \\xa0."""
    assert normalize_name("Justin Jefferson\xa0Min") == "justin jefferson min"
    assert normalize_name("Ja'Marr Chase\xa0Cin") == "jamarr chase cin"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Tre'von Moehrig", "trevon moehrig"),
        ("Henry To'oTo'o", "henry tootoo"),
        ("D'Angelo Ponds", "dangelo ponds"),
        ("Ja'Marr Chase", "jamarr chase"),
    ],
)
def test_apostrophes_and_internal_capitals(raw, expected):
    assert normalize_name(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Kool-Aid McKinstry", "kool aid mckinstry"),
        ("Akeem Davis-Gaither", "akeem davis gaither"),
        ("Amon-Ra St. Brown", "amon ra st brown"),
    ],
)
def test_hyphens_become_spaces(raw, expected):
    assert normalize_name(raw) == expected


def test_hyphen_and_space_spellings_collapse_together():
    assert normalize_name("Davis-Gaither") == normalize_name("Davis Gaither")


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Will Anderson Jr.", "will anderson"),
        ("Mack Wilson Sr.", "mack wilson"),
        ("Jermaine Johnson II", "jermaine johnson"),
        ("Jessie Bates III", "jessie bates"),
        ("Some Player IV", "some player"),
        ("Another Guy V", "another guy"),
    ],
)
def test_suffixes_are_stripped(raw, expected):
    assert normalize_name(raw) == expected


def test_suffix_variants_collapse_together():
    # the same human written three ways across three sources
    assert (
        normalize_name("Byron Murphy Jr.")
        == normalize_name("Byron Murphy II")
        == normalize_name("Byron Murphy")
    )


def test_periods_are_removed():
    assert normalize_name("T.J. Watt") == "tj watt"
    assert normalize_name("A.J. Brown") == "aj brown"


def test_accents_are_folded():
    assert normalize_name("San Nicolás") == "san nicolas"


def test_case_and_whitespace_insensitive():
    assert normalize_name("  FRED   WARNER  ") == "fred warner"


def test_a_bare_suffix_is_not_stripped_to_nothing():
    # a one-token name that happens to be a suffix word must survive
    assert normalize_name("V") == "v"


# --- team normalization (tie-breaking only) --------------------------------

@pytest.mark.parametrize(
    "a,b",
    [("WAS", "WSH"), ("JAC", "JAX"), ("LA", "LAR"), ("OAK", "LV"), ("SD", "LAC")],
)
def test_team_aliases_collapse(a, b):
    assert normalize_team(a) == normalize_team(b)


def test_unknown_team_passes_through_uppercased():
    assert normalize_team(" buf ") == "BUF"


# --- match classification --------------------------------------------------

def test_exact_name_and_slot_agreement_auto_matches():
    row = src("Fred Warner", "LB", "LB")
    cands = [(espn(1, "Fred Warner", "LB", "LB"), 1.0)]
    verdict, _reason, chosen, score = classify(row, cands)
    assert verdict == "matched"
    assert chosen.espn_id == 1
    assert score == 1.0


def test_slot_mismatch_goes_to_review_even_on_exact_name():
    """Travis Hunter is a real two-way case: CB in one source, WR in ESPN."""
    row = src("Travis Hunter", "CB", "DB")
    cands = [(espn(9, "Travis Hunter", "WR", "WR"), 1.0)]
    verdict, reason, chosen, _ = classify(row, cands)
    assert verdict == "review"
    assert "slot mismatch" in reason
    assert chosen.espn_id == 9  # still shown as a candidate


def test_two_players_sharing_a_name_go_to_review():
    """Lamar Jackson: a QB and a CB both exist in the ESPN universe."""
    row = src("Lamar Jackson", "QB", "QB")
    cands = [(espn(1, "Lamar Jackson", "QB", "QB"), 1.0),
             (espn(2, "Lamar Jackson", "CB", "DB"), 1.0)]
    verdict, reason, _, _ = classify(row, cands)
    assert verdict == "review"
    assert "ambiguous" in reason and "share this name" in reason


def test_team_breaks_a_tie_for_display_but_still_reviews():
    row = src("Lamar Jackson", "QB", "QB", team="BAL")
    cands = [(espn(1, "Lamar Jackson", "QB", "QB", team="BAL"), 1.0),
             (espn(2, "Lamar Jackson", "CB", "DB", team="NE"), 1.0)]
    verdict, _reason, chosen, _ = classify(row, cands)
    assert verdict == "review", "team may disambiguate display order, never auto-match"
    assert chosen.espn_id == 1


def test_team_alias_is_used_when_breaking_ties():
    row = src("Some Player", "WR", "WR", team="WAS")  # source spelling
    cands = [(espn(1, "Some Player", "WR", "WR", team="WSH"), 1.0),  # ESPN spelling
             (espn(2, "Some Player", "WR", "WR", team="DAL"), 1.0)]
    _, _, chosen, _ = classify(row, cands)
    assert chosen.espn_id == 1


def test_score_below_auto_floor_goes_to_review():
    row = src("Jonathan Taylor", "RB", "RB")
    cands = [(espn(1, "Jonathon Tayler", "RB", "RB"), 0.90)]
    verdict, reason, _, _ = classify(row, cands)
    assert verdict == "review"
    assert str(AUTO_FUZZY_FLOOR) in reason


def test_no_candidates_is_unmatched():
    verdict, _reason, chosen, score = classify(src("Nobody At All", "WR", "WR"), [])
    assert verdict == "unmatched"
    assert chosen is None and score == 0.0


def test_high_fuzzy_score_with_slot_agreement_auto_matches():
    row = src("Chris Olave", "WR", "WR")
    cands = [(espn(1, "Christopher Olave", "WR", "WR"), 0.96)]
    verdict, _, chosen, _ = classify(row, cands)
    assert verdict == "matched"
    assert chosen.espn_id == 1


def test_ambiguity_beats_slot_agreement():
    """Two equally-scoring candidates review even if one slot-matches."""
    row = src("Marcus Harris", "CB", "DB")
    cands = [(espn(1, "Marcus Harris", "CB", "DB"), 1.0),
             (espn(2, "Marcus Harris", "DT", "DL"), 1.0)]
    verdict, reason, _, _ = classify(row, cands)
    assert verdict == "review"
    assert "ambiguous" in reason
