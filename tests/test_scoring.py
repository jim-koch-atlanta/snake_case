"""Tests for the custom-scoring engine. Written before the implementation.

Every expected total below is hand-computed from the league's actual scoring
values. The three named cases exist because each encodes a mistake that would
silently reorder the draft board:

  * receptions at 0.2 (not 0.5, not 1.0) — this is not a half-PPR league
  * HALFSK: ESPN scores HALF sacks at 1.4, projections report WHOLE sacks
  * solo and assisted tackles score differently (1.1 vs 0.8) and must never be
    summed upstream
"""

import tomllib
from pathlib import Path

import pytest

from engine.scoring import (
    RULES,
    score_breakdown,
    score_stat_line,
    unscored_stats,
)

EXAMPLE = Path(__file__).resolve().parent.parent / "docs" / "league-config.toml.example"

# The league's real values, written out so the arithmetic below is checkable by
# hand. test_scoring_values_match_the_config guards against drift.
SCORING = {
    "passing": {"yards": 0.06, "td": 6.0, "int": -3.0, "2pc": 2.0, "sacked": -0.5},
    "rushing": {"yards": 0.1, "td": 6.0, "2pr": 2.0},
    "receiving": {"reception": 0.2, "yards": 0.1, "td": 6.0, "2pre": 2.0},
    "kicker": {"pat": 1.0, "fgy": 0.1, "fg_0_39": 0.0, "fg_40_49": 0.0,
               "fg_50_plus": 0.0, "missed_fg": 0.0},
    "idp": {"half_sack": 1.4, "solo_tackle": 1.1, "assisted_tackle": 0.8,
            "tackle_for_loss": 0.3, "pass_defended": 1.4, "interception": 3.5,
            "forced_fumble": 1.8, "fumble_recovery": 1.8, "safety": 4.0,
            "blocked_kick": 6.0},
    "misc": {"kick_return_yards": 0.04, "punt_return_yards": 0.08,
             "kick_return_td": 6.0, "punt_return_td": 6.0,
             "fumble_recovered_td": 6.0, "fumbles_lost": -3.0,
             "interception_return_td": 6.0, "fumble_return_td": 6.0,
             "blocked_kick_return_td": 6.0, "two_pt_return": 2.0,
             "one_pt_safety": 1.0},
}


def test_scoring_values_match_the_config():
    """If the league config changes, these tests must be re-derived by hand."""
    cfg = tomllib.loads(EXAMPLE.read_text())["scoring"]
    for section, rules in SCORING.items():
        for key, value in rules.items():
            assert cfg[section][key] == value, f"scoring.{section}.{key} drifted"


# --- named case 1: high-reception WR at 0.2 per reception -------------------

def test_high_reception_wr_scores_receptions_at_the_config_value():
    """100 catches is 20 points here, not 50 (half-PPR) or 100 (full PPR).

        100 rec  x 0.2 =  20.0
       1200 yds  x 0.1 = 120.0
          8 TD   x 6.0 =  48.0
                        -------
                         188.0
    """
    stats = {"receptions": 100, "receiving_yards": 1200, "receiving_td": 8}
    assert score_stat_line(stats, SCORING) == pytest.approx(188.0)


def test_reception_value_is_not_a_ppr_preset():
    stats = {"receptions": 100}
    assert score_stat_line(stats, SCORING) == pytest.approx(20.0)
    # the three values this must never silently become
    assert score_stat_line(stats, SCORING) != pytest.approx(50.0)   # half-PPR
    assert score_stat_line(stats, SCORING) != pytest.approx(100.0)  # full PPR
    assert score_stat_line(stats, SCORING) != pytest.approx(0.0)    # standard


def test_changing_only_the_reception_value_rederives_the_total():
    """CLAUDE.md: changing that one number must re-derive every valuation."""
    stats = {"receptions": 100, "receiving_yards": 1200, "receiving_td": 8}
    half_ppr = {**SCORING, "receiving": {**SCORING["receiving"], "reception": 0.5}}
    assert score_stat_line(stats, half_ppr) == pytest.approx(218.0)


# --- named case 2: 12-sack DL and the half-sack unit ------------------------

def test_twelve_sack_dl_uses_half_sack_units():
    """ESPN's HALFSK is 1.4 per HALF sack; projections report WHOLE sacks.

        12 sacks = 24 half-sack units x 1.4 = 33.6   <- correct
        treating 12 sacks as 12 units     x 1.4 = 16.8   <- the bug
    """
    stats = {"sacks": 12}
    assert score_stat_line(stats, SCORING) == pytest.approx(33.6)
    assert score_stat_line(stats, SCORING) != pytest.approx(16.8)


def test_full_dl_stat_line():
    """
        12 sacks -> 24 units x 1.4 = 33.6
        40 solo           x 1.1    = 44.0
        20 assists        x 0.8    = 16.0
        15 TFL            x 0.3    =  4.5
                                    ------
                                     98.1
    """
    stats = {"sacks": 12, "solo_tackles": 40, "assisted_tackles": 20,
             "tackles_for_loss": 15}
    assert score_stat_line(stats, SCORING) == pytest.approx(98.1)


def test_half_sack_conversion_is_visible_in_the_breakdown():
    b = score_breakdown({"sacks": 12}, SCORING)
    assert b["sacks"] == pytest.approx(33.6)


def test_half_sack_handles_fractional_sacks():
    # projections are decimals: 12.5 sacks = 25 units = 35.0
    assert score_stat_line({"sacks": 12.5}, SCORING) == pytest.approx(35.0)


# --- named case 3: tackle-heavy LB, solo and assists scored separately ------

def test_tackle_heavy_lb_scores_solo_and_assists_separately():
    """4for4 gives Tackles and Assists as distinct columns. Never sum them.

        100 solo    x 1.1 = 110.0
         40 assists x 0.8 =  32.0
                            ------
                             142.0

    Summing to 140 first would give 154.0 (all solo) or 112.0 (all assist).
    """
    stats = {"solo_tackles": 100, "assisted_tackles": 40}
    assert score_stat_line(stats, SCORING) == pytest.approx(142.0)
    assert score_stat_line(stats, SCORING) != pytest.approx(154.0)
    assert score_stat_line(stats, SCORING) != pytest.approx(112.0)


def test_full_lb_stat_line():
    """
        100 solo    x 1.1 = 110.0
         40 assists x 0.8 =  32.0
          2 sacks -> 4 u  x 1.4 = 5.6
          5 PD      x 1.4 =   7.0
          1 INT     x 3.5 =   3.5
          1 FF      x 1.8 =   1.8
          1 FR      x 1.8 =   1.8
                            ------
                             161.7
    """
    stats = {"solo_tackles": 100, "assisted_tackles": 40, "sacks": 2,
             "passes_defended": 5, "interceptions": 1, "forced_fumbles": 1,
             "fumble_recoveries": 1}
    assert score_stat_line(stats, SCORING) == pytest.approx(161.7)


def test_solo_and_assist_are_distinct_rules():
    solo = score_stat_line({"solo_tackles": 10}, SCORING)
    asst = score_stat_line({"assisted_tackles": 10}, SCORING)
    assert solo == pytest.approx(11.0)
    assert asst == pytest.approx(8.0)
    assert solo != asst


# --- named case 4: kicker, fgy is per FG YARD not per FG made ---------------

def test_kicker_scores_field_goal_yardage_not_field_goal_count():
    """`fgy` is ESPN's FGY = "FG Made Yards". Confirmed with the commissioner
    2026-08-20; the config comment previously read "per FG made".

        40 PAT      x 1.0 =  40.0
      1200 FG yards x 0.1 = 120.0
                            ------
                             160.0

    Read as per-FG-*made*, the same kicker's ~31 field goals would score 3.1
    and the total would collapse to ~43 — kickers would rank on PATs alone.
    """
    stats = {"pat_made": 40, "field_goal_yards": 1200}
    assert score_stat_line(stats, SCORING) == pytest.approx(160.0)


def test_field_goal_count_is_not_a_scored_stat():
    """4for4 supplies FG counts and no yardage, so its FG column must not be
    silently treated as `field_goal_yards`."""
    assert "fg_made" not in [r.stat for r in RULES]
    assert score_stat_line({"fg_made": 31}, SCORING) == pytest.approx(0.0)
    assert "fg_made" in unscored_stats({"fg_made": 31})


def test_zero_valued_kicker_range_buckets_contribute_nothing():
    """fg_0_39 / fg_40_49 / fg_50_plus are all 0.0 in this league: distance is
    paid through yardage instead, so the buckets must not double-count."""
    stats = {"fg_made_0_39": 19, "fg_made_40_49": 9, "fg_made_50_plus": 7}
    assert score_stat_line(stats, SCORING) == pytest.approx(0.0)


# --- offense -----------------------------------------------------------------

def test_qb_stat_line():
    """
        4000 pass yds x 0.06 = 240.0
          30 pass TD  x 6.0  = 180.0
          10 INT      x -3.0 = -30.0
         500 rush yds x 0.1  =  50.0
           5 rush TD  x 6.0  =  30.0
                               ------
                                470.0
    """
    stats = {"pass_yards": 4000, "pass_td": 30, "interceptions_thrown": 10,
             "rush_yards": 500, "rush_td": 5}
    assert score_stat_line(stats, SCORING) == pytest.approx(470.0)


def test_fumbles_lost_is_negative():
    assert score_stat_line({"fumbles_lost": 3}, SCORING) == pytest.approx(-9.0)


def test_negative_rules_reduce_the_total():
    clean = score_stat_line({"pass_td": 10}, SCORING)
    picked = score_stat_line({"pass_td": 10, "interceptions_thrown": 5}, SCORING)
    assert picked == pytest.approx(clean - 15.0)


# --- unscored stats ----------------------------------------------------------

def test_stats_with_no_rule_score_zero():
    """Any stat in the projection files with no matching rule contributes 0."""
    base = score_stat_line({"solo_tackles": 10}, SCORING)
    with_extra = score_stat_line(
        {"solo_tackles": 10, "qb_hits": 25, "snap_pct": 92, "pass_attempts": 500},
        SCORING,
    )
    assert with_extra == pytest.approx(base)


def test_unscored_stats_are_reported_by_name():
    names = unscored_stats({"solo_tackles": 10, "qb_hits": 25, "snap_pct": 92})
    assert set(names) == {"qb_hits", "snap_pct"}


def test_unscored_stats_is_empty_when_everything_maps():
    assert unscored_stats({"solo_tackles": 1, "sacks": 2, "receptions": 3}) == []


def test_a_rule_missing_from_the_config_scores_zero_rather_than_inventing():
    """No scoring value in the TOML means no points — never a default."""
    trimmed = {**SCORING, "idp": {k: v for k, v in SCORING["idp"].items()
                                  if k != "blocked_kick"}}
    assert score_stat_line({"blocked_kicks": 3}, trimmed) == pytest.approx(0.0)
    assert score_stat_line({"blocked_kicks": 3}, SCORING) == pytest.approx(18.0)


# --- breakdown ---------------------------------------------------------------

def test_breakdown_sums_to_the_total():
    stats = {"solo_tackles": 100, "assisted_tackles": 40, "sacks": 2,
             "passes_defended": 5, "interceptions": 1}
    b = score_breakdown(stats, SCORING)
    assert sum(b.values()) == pytest.approx(score_stat_line(stats, SCORING))


def test_breakdown_omits_stats_that_score_nothing():
    b = score_breakdown({"solo_tackles": 10, "qb_hits": 25}, SCORING)
    assert "qb_hits" not in b


def test_empty_stat_line_scores_zero():
    assert score_stat_line({}, SCORING) == pytest.approx(0.0)
    assert score_breakdown({}, SCORING) == {}


# --- rule table integrity ----------------------------------------------------

def test_rule_stat_keys_are_unique():
    keys = [r.stat for r in RULES]
    assert len(keys) == len(set(keys))


def test_only_sacks_uses_a_unit_multiplier():
    """The half-sack conversion is the single place units != 1."""
    multiplied = {r.stat: r.units_per_stat for r in RULES if r.units_per_stat != 1.0}
    assert multiplied == {"sacks": 2.0}


def test_every_rule_points_at_a_real_config_key():
    cfg = tomllib.loads(EXAMPLE.read_text())["scoring"]
    for rule in RULES:
        node = cfg
        for part in rule.path:
            assert part in node, f"rule {rule.stat} -> scoring.{'.'.join(rule.path)} missing"
            node = node[part]
        assert isinstance(node, (int, float))


def test_ff_pts_is_never_a_scored_stat():
    """CLAUDE.md: never use a provider's precomputed points."""
    assert all("ff" not in r.stat.lower().split("_") for r in RULES)
    assert score_stat_line({"ff_pts": 300.0}, SCORING) == pytest.approx(0.0)
