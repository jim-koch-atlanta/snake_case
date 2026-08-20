"""Custom scoring: stat line x [scoring] rules -> fantasy points.

Pure functions, zero I/O (architecture invariant #1). The caller supplies both
the stat line and the scoring table read from docs/league-config.toml, so
changing a single scoring value re-derives every valuation with no code edit.

Never use a provider's precomputed points (`FF Pts` in every 4for4 file) — that
is their scoring, not ours. This module is the only place points are produced.

Two things here are load-bearing and easy to get wrong:

  * `sacks` are reported by projections as WHOLE sacks, but the league scores
    ESPN's HALFSK stat at 1.4 per HALF sack. 12 sacks = 24 units = 33.6 points,
    not 16.8. That is the only place `units_per_stat` is not 1.
  * solo and assisted tackles are SEPARATE rules (1.1 vs 0.8) fed by separate
    4for4 columns. Summing them upstream loses ~20 points on a 140-tackle LB.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

Scoring = Mapping[str, Mapping[str, float]]
StatLine = Mapping[str, float]


@dataclass(frozen=True)
class ScoringRule:
    """One canonical stat and the `[scoring]` key it is paid by.

    `units_per_stat` converts the projected stat into the unit the scoring rule
    is denominated in. It is 1.0 everywhere except sacks.
    """

    stat: str
    path: tuple[str, ...]
    units_per_stat: float = 1.0


RULES: tuple[ScoringRule, ...] = (
    # --- passing ---
    ScoringRule("pass_yards", ("passing", "yards")),
    ScoringRule("pass_td", ("passing", "td")),
    ScoringRule("interceptions_thrown", ("passing", "int")),
    ScoringRule("pass_2pt", ("passing", "2pc")),
    ScoringRule("times_sacked", ("passing", "sacked")),
    # --- rushing ---
    ScoringRule("rush_yards", ("rushing", "yards")),
    ScoringRule("rush_td", ("rushing", "td")),
    ScoringRule("rush_2pt", ("rushing", "2pr")),
    # --- receiving ---
    # 0.2 per reception. NOT half-PPR — see CLAUDE.md League facts.
    ScoringRule("receptions", ("receiving", "reception")),
    ScoringRule("receiving_yards", ("receiving", "yards")),
    ScoringRule("receiving_td", ("receiving", "td")),
    ScoringRule("receiving_2pt", ("receiving", "2pre")),
    # --- kicking ---
    ScoringRule("pat_made", ("kicker", "pat")),
    # `fgy` is ESPN's "FG Made Yards" (FGY) — points per FG YARD, not per FG
    # made (confirmed 2026-08-20). 4for4 supplies FG counts with no yardage, so
    # kicker stat lines must come from ESPN `kona_player_info` stat id 214.
    ScoringRule("field_goal_yards", ("kicker", "fgy")),
    ScoringRule("fg_made_0_39", ("kicker", "fg_0_39")),
    ScoringRule("fg_made_40_49", ("kicker", "fg_40_49")),
    ScoringRule("fg_made_50_plus", ("kicker", "fg_50_plus")),
    ScoringRule("fg_missed", ("kicker", "missed_fg")),
    # --- IDP ---
    # HALFSK: projections give WHOLE sacks, the rule pays per HALF sack.
    ScoringRule("sacks", ("idp", "half_sack"), units_per_stat=2.0),
    ScoringRule("solo_tackles", ("idp", "solo_tackle")),
    ScoringRule("assisted_tackles", ("idp", "assisted_tackle")),
    ScoringRule("tackles_for_loss", ("idp", "tackle_for_loss")),
    ScoringRule("passes_defended", ("idp", "pass_defended")),
    ScoringRule("interceptions", ("idp", "interception")),
    ScoringRule("forced_fumbles", ("idp", "forced_fumble")),
    ScoringRule("fumble_recoveries", ("idp", "fumble_recovery")),
    ScoringRule("safeties", ("idp", "safety")),
    ScoringRule("blocked_kicks", ("idp", "blocked_kick")),
    # --- returns / misc ---
    ScoringRule("kick_return_yards", ("misc", "kick_return_yards")),
    ScoringRule("punt_return_yards", ("misc", "punt_return_yards")),
    ScoringRule("kick_return_td", ("misc", "kick_return_td")),
    ScoringRule("punt_return_td", ("misc", "punt_return_td")),
    ScoringRule("fumble_recovered_td", ("misc", "fumble_recovered_td")),
    ScoringRule("fumbles_lost", ("misc", "fumbles_lost")),
    ScoringRule("interception_return_td", ("misc", "interception_return_td")),
    ScoringRule("fumble_return_td", ("misc", "fumble_return_td")),
    ScoringRule("blocked_kick_return_td", ("misc", "blocked_kick_return_td")),
    ScoringRule("two_pt_return", ("misc", "two_pt_return")),
    ScoringRule("one_pt_safety", ("misc", "one_pt_safety")),
    # 4for4 reports a single `DefTD` without itemizing whether it was an
    # interception or a fumble return. Both rules are 6.0, so the total is
    # identical either way; mapped here so the column is not silently dropped.
    ScoringRule("defensive_td", ("misc", "interception_return_td")),
)

_RULES_BY_STAT: dict[str, ScoringRule] = {r.stat: r for r in RULES}


def _rule_value(scoring: Scoring, rule: ScoringRule) -> float | None:
    """Look up a rule's points value, or None if the config does not define it.

    A rule absent from the config scores nothing. We never substitute a default
    — an invented scoring value produces confident, wrong valuations.
    """
    node: object = scoring
    for part in rule.path:
        if not isinstance(node, Mapping) or part not in node:
            return None
        node = node[part]
    return float(node) if isinstance(node, (int, float)) else None


def score_breakdown(stats: StatLine, scoring: Scoring) -> dict[str, float]:
    """Points contributed by each stat. Omits stats that contribute nothing."""
    out: dict[str, float] = {}
    for stat, raw in stats.items():
        rule = _RULES_BY_STAT.get(stat)
        if rule is None:
            continue
        value = _rule_value(scoring, rule)
        if value is None:
            continue
        points = float(raw) * rule.units_per_stat * value
        if points:
            out[stat] = points
    return out


def score_stat_line(stats: StatLine, scoring: Scoring) -> float:
    """Total fantasy points for one stat line under our scoring rules."""
    return sum(score_breakdown(stats, scoring).values())


def unscored_stats(stats: StatLine) -> list[str]:
    """Stats present in the line that no scoring rule pays for.

    These contribute zero. Report them by name rather than dropping them
    silently — an unmapped stat is either genuinely unscored (QB hits, snap
    share) or a mapping we forgot to write.
    """
    return sorted(s for s in stats if s not in _RULES_BY_STAT)


def known_stats() -> list[str]:
    """Every canonical stat the engine can score."""
    return sorted(_RULES_BY_STAT)
