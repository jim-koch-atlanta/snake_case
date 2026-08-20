"""Load stat-level projections, resolve them to ESPN ids, and score them.

This is the wire between three things that already work independently:

    data/4for4/*.csv        stat lines, in 4for4's column vocabulary
    data/espn/players.json  stat lines for KICKERS (see below)
    data/crosswalk.csv      source name -> ESPN player id
    engine/scoring.py       stat line x [scoring] -> points

Output is a valued player pool keyed by ESPN player id, because that is what
draft-day picks arrive as.

Kickers are the one exception to "4for4 for everything": our `kicker.fgy` rule
pays per field-goal YARD, and 4for4 only reports FG counts. ESPN carries the
yardage (stat id 214), so kickers come from ESPN and 4for4's K rows are skipped.

I/O and vocabulary translation live here; the math stays in engine/ (invariant
#1). Any projection row that cannot be resolved to an ESPN id is a hard error,
never a silent drop (invariant #3).

    uv run python -m sources.projections            # summary + top 20 per slot
    uv run python -m sources.projections --slot DL  # one slot
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from engine.positions import UnknownPositionError, slot_for_position
from engine.scoring import score_stat_line

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CONFIG = ROOT / "docs" / "league-config.toml"
CROSSWALK = DATA / "crosswalk.csv"
ESPN_PLAYERS = DATA / "espn" / "players.json"
OFFENSE_CSV = DATA / "4for4" / "4for4_projections.csv"
IDP_CSV = DATA / "4for4" / "4for4-fantasy-football-projections-{group}-2026-table.csv"
IDP_GROUPS = ("db", "dl", "lb")

ESPN_KICKER_POSITION_ID = 5

#: 4for4 offense columns -> canonical stat names. Columns absent here are real
#: stats this league does not score (Pass Comp/Att, Rush Att, Pa1D/Ru1D/Rec1D).
#: `FF Pts` is never mapped — that is 4for4's scoring, not ours.
OFFENSE_COLUMNS: Mapping[str, str] = {
    "Pass Yds": "pass_yards",
    "Pass TD": "pass_td",
    "INT": "interceptions_thrown",
    "Rush Yds": "rush_yards",
    "Rush TD": "rush_td",
    "Rec": "receptions",
    "Rec Yds": "receiving_yards",
    "Rec TD": "receiving_td",
    "Fum": "fumbles_lost",
}

#: 4for4 IDP columns -> canonical stat names. `Tackles` is SOLO tackles and is
#: scored separately from `Assists` (1.1 vs 0.8) — never sum them. `Sacks` are
#: WHOLE sacks; engine/scoring.py applies the half-sack unit conversion.
IDP_COLUMNS: Mapping[str, str] = {
    "Tackles": "solo_tackles",
    "Assists": "assisted_tackles",
    "Sacks": "sacks",
    "TFL": "tackles_for_loss",
    "INT": "interceptions",
    "PD": "passes_defended",
    "FFum": "forced_fumbles",
    "FR": "fumble_recoveries",
    "Safety": "safeties",
    "DefTD": "defensive_td",
}

#: ESPN stat ids -> canonical stat names, for kickers. Decoded empirically; see
#: docs/session-log.md. 214 is FG made yards, 86 is PATs made.
ESPN_KICKER_STATS: Mapping[str, str] = {
    "86": "pat_made",
    "214": "field_goal_yards",
}


class ProjectionError(Exception):
    """Raised when projections cannot be loaded or resolved safely."""


@dataclass(frozen=True)
class ValuedPlayer:
    """One player, scored under our league's rules."""

    espn_id: int
    name: str
    slot: str
    position: str
    team: str
    points: float
    stats: Mapping[str, float]
    source: str


@dataclass
class LoadReport:
    """What happened during a load. Printed rather than thrown away."""

    counts: dict[str, int] = field(default_factory=dict)
    unmatched: list[tuple[str, str]] = field(default_factory=list)
    unscored: set[str] = field(default_factory=set)
    skipped_kickers: int = 0
    off_pool: list[str] = field(default_factory=list)
    #: One ESPN id projected under two slots — a two-way player. Surfaced for a
    #: human decision, never silently resolved.
    duplicate_ids: list[tuple[int, str, list[str]]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# inputs
# ---------------------------------------------------------------------------

def load_scoring(path: Path = CONFIG) -> Mapping:
    """The `[scoring]` table from the league config."""
    if not path.exists():
        raise ProjectionError(f"league config not found: {path}")
    with path.open("rb") as f:
        return tomllib.load(f)["scoring"]


def load_crosswalk(path: Path = CROSSWALK) -> dict[tuple[str, str], int]:
    """(source, source_key) -> espn_id, from the hand-reviewed crosswalk."""
    if not path.exists():
        raise ProjectionError(f"crosswalk not found: {path} — run build_crosswalk")
    out: dict[tuple[str, str], int] = {}
    with path.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            espn_id = (row.get("espn_id") or "").strip()
            if espn_id.isdigit():
                out[(row["source"], row["source_key"])] = int(espn_id)
    if not out:
        raise ProjectionError(f"crosswalk at {path} produced no usable rows")
    return out


def espn_season_stats(player: dict, season: int = 2026) -> dict[str, float]:
    """Projected SEASON stat line for an ESPN player, by raw stat id.

    ESPN returns several stat blocks per player. The one we want has
    `statSourceId == 1` (projected, not actual) and `statSplitTypeId == 0`
    (season total, not per-game — the per-game block is statSplitTypeId 1 and
    would understate everything by ~17x).
    """
    for s in player.get("stats") or []:
        if (
            s.get("statSourceId") == 1
            and s.get("seasonId") == season
            and s.get("statSplitTypeId") == 0
            and s.get("stats")
        ):
            return {k: float(v) for k, v in s["stats"].items()}
    return {}


# ---------------------------------------------------------------------------
# TODO(jim): two functions left for you — see tests/test_projections.py
# ---------------------------------------------------------------------------

def stat_line_from_row(row: Mapping[str, str], columns: Mapping[str, str]) -> dict[str, float]:
    """Translate one CSV row into a canonical stat line.

    `columns` maps a source column name to our canonical stat name, e.g.
    ``{"Tackles": "solo_tackles"}``. For every column in `columns` that is
    present in `row`, emit ``canonical_name -> float(value)``. Columns missing
    from `row` are skipped. Blank cells count as 0.0 — ``float("")`` raises.

    Everything the engine scores flows through here, so this is the seam that
    keeps 4for4's vocabulary out of engine/.

    >>> stat_line_from_row({"Tackles": "38.1", "Assists": ""}, IDP_COLUMNS)
    {'solo_tackles': 38.1, 'assisted_tackles': 0.0}
    """
    result = {}
    for column_name, canonical_name in columns.items():
        if column_name in row:
            result[canonical_name] = float(row[column_name] or 0)
    return result


def top_by_slot(players: list[ValuedPlayer], slot: str, limit: int = 20) -> list[ValuedPlayer]:
    """The `limit` highest-scoring players in one roster slot, best first.

    Used to eyeball the board and to find replacement baselines (CLAUDE.md:
    replacement is DL24 / LB24 / DB24, so ``top_by_slot(players, "DL", 24)[-1]``
    is the DL baseline).
    """
    players_for_slot: list[ValuedPlayer] = [p for p in players if p.slot == slot]
    players_for_slot.sort(key = lambda p : -p.points)
    return players_for_slot[:limit]


# ---------------------------------------------------------------------------
# per-source loaders
# ---------------------------------------------------------------------------

def _resolve(
    crosswalk: dict[tuple[str, str], int],
    source: str,
    key: str,
    name: str,
    report: LoadReport,
) -> int | None:
    espn_id = crosswalk.get((source, key))
    if espn_id is None:
        report.unmatched.append((source, name))
    return espn_id


def load_offense(
    crosswalk: dict[tuple[str, str], int],
    scoring: Mapping,
    report: LoadReport,
    path: Path = OFFENSE_CSV,
) -> list[ValuedPlayer]:
    """4for4 offense. Kickers are skipped — they come from ESPN instead."""
    out: list[ValuedPlayer] = []
    with path.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            position = (row.get("Pos") or "").strip()
            name = (row.get("Player") or "").strip()
            if not name or not position:
                continue
            if position.upper() == "K":
                report.skipped_kickers += 1
                continue
            espn_id = _resolve(crosswalk, "4for4_offense", (row.get("PID") or name).strip(), name, report)
            if espn_id is None:
                continue
            stats = stat_line_from_row(row, OFFENSE_COLUMNS)
            report.unscored |= unmapped_columns(row, OFFENSE_COLUMNS)
            out.append(ValuedPlayer(
                espn_id=espn_id, name=name, slot=slot_for_position(position),
                position=position, team=(row.get("Team") or "").strip(),
                points=score_stat_line(stats, scoring), stats=stats,
                source="4for4_offense",
            ))
    report.counts["4for4_offense"] = len(out)
    return out


def load_idp(
    crosswalk: dict[tuple[str, str], int],
    scoring: Mapping,
    report: LoadReport,
) -> list[ValuedPlayer]:
    """4for4 IDP, across the db/dl/lb tables."""
    out: list[ValuedPlayer] = []
    for group in IDP_GROUPS:
        path = Path(str(IDP_CSV).format(group=group))
        with path.open(newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                name = (row.get("Player") or "").strip()
                position = (row.get("Position") or "").strip()
                if not name or not position:
                    continue
                team = (row.get("Team") or "").strip()
                key = f"{group}:{name}|{team}"
                espn_id = _resolve(crosswalk, "4for4_idp", key, name, report)
                if espn_id is None:
                    continue
                stats = stat_line_from_row(row, IDP_COLUMNS)
                report.unscored |= unmapped_columns(row, IDP_COLUMNS)
                out.append(ValuedPlayer(
                    espn_id=espn_id, name=name, slot=slot_for_position(position),
                    position=position, team=team,
                    points=score_stat_line(stats, scoring), stats=stats,
                    source="4for4_idp",
                ))
    report.counts["4for4_idp"] = len(out)
    return out


def load_kickers(
    scoring: Mapping,
    report: LoadReport,
    path: Path = ESPN_PLAYERS,
) -> list[ValuedPlayer]:
    """Kickers from ESPN — the only source with field-goal YARDAGE.

    Already keyed by ESPN id, so no crosswalk lookup is needed. Kickers with no
    projected FG yardage are camp bodies; they are counted, not scored.
    """
    if not path.exists():
        raise ProjectionError(f"ESPN player cache not found: {path} — run build_crosswalk --refresh")
    raw = json.loads(path.read_text())
    out: list[ValuedPlayer] = []
    unprojected = 0
    for entry in raw.get("players", []):
        p = entry.get("player", {})
        if p.get("defaultPositionId") != ESPN_KICKER_POSITION_ID:
            continue
        espn_stats = espn_season_stats(p)
        if not espn_stats.get("214"):
            unprojected += 1
            continue
        stats = {
            canonical: espn_stats.get(stat_id, 0.0)
            for stat_id, canonical in ESPN_KICKER_STATS.items()
        }
        out.append(ValuedPlayer(
            espn_id=int(p["id"]), name=p.get("fullName", ""), slot="K",
            position="K", team="", points=score_stat_line(stats, scoring),
            stats=stats, source="espn_kicker",
        ))
    report.counts["espn_kicker"] = len(out)
    report.counts["espn_kicker_unprojected"] = unprojected
    return out


#: Identity/metadata columns — not stats, so their absence from a scoring rule
#: is not interesting. `FF Pts` is listed here so it is never reported as an
#: "unscored stat"; it is deliberately ignored everywhere.
METADATA_COLUMNS = frozenset({
    "#", "PID", "Player", "Pos", "Position", "Team", "Bye", "BYE",
    "FF Pts", "ADP", "Health", "C",
})


def unmapped_columns(row: Mapping[str, str], columns: Mapping[str, str]) -> set[str]:
    """Stat columns present in the file that no scoring rule pays for.

    Derived from column NAMES so it never has to parse a value — some columns
    (Health="A", C="+") are not numeric at all.
    """
    return {k for k in row if k not in METADATA_COLUMNS and k not in columns}


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def load_projections(strict: bool = True) -> tuple[list[ValuedPlayer], LoadReport]:
    """Every projected player, scored, keyed by ESPN id.

    Raises ProjectionError if any projection row fails to resolve to an ESPN id
    (invariant #3) — collecting them all first, so one run shows every problem.
    Pass ``strict=False`` to report instead of raise.
    """
    scoring = load_scoring()
    crosswalk = load_crosswalk()
    report = LoadReport()

    players = (
        load_offense(crosswalk, scoring, report)
        + load_idp(crosswalk, scoring, report)
        + load_kickers(scoring, report)
    )

    if report.unmatched and strict:
        preview = ", ".join(f"{s}/{n}" for s, n in report.unmatched[:10])
        raise ProjectionError(
            f"{len(report.unmatched)} projection row(s) could not be resolved to an "
            f"ESPN id — a silently dropped player is one you never see on the board. "
            f"First few: {preview}. Re-run build_crosswalk and hand-review."
        )

    by_id: dict[int, list[ValuedPlayer]] = {}
    for p in players:
        by_id.setdefault(p.espn_id, []).append(p)
    report.duplicate_ids = [
        (espn_id, group[0].name, [f"{p.source}:{p.slot}:{p.points:.1f}" for p in group])
        for espn_id, group in sorted(by_id.items())
        if len(group) > 1
    ]

    report.counts["total"] = len(players)
    report.counts["distinct_espn_ids"] = len(by_id)
    return players, report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--slot", help="only show this roster slot (QB/RB/WR/TE/K/DL/LB/DB)")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--lenient", action="store_true", help="report unmatched instead of failing")
    args = ap.parse_args()

    try:
        players, report = load_projections(strict=not args.lenient)
    except (ProjectionError, UnknownPositionError, FileNotFoundError) as e:
        print(f"PROJECTION ERROR: {e}", file=sys.stderr)
        return 1

    print("loaded:")
    for k, v in report.counts.items():
        print(f"  {k:<24} {v}")
    if report.skipped_kickers:
        print(f"  {'4for4 K rows skipped':<24} {report.skipped_kickers}  (no FG yardage; ESPN used instead)")
    if report.unmatched:
        print(f"  {'UNRESOLVED':<24} {len(report.unmatched)}")
    if report.unscored:
        print("\nstats present in the projections with no scoring rule (score 0):")
        print(f"  {', '.join(sorted(report.unscored))}")

    if report.duplicate_ids:
        print("\nplayers projected under MORE THAN ONE slot — two-way players.")
        print("ESPN's classification governs which slot they can legally fill:")
        for espn_id, name, entries in report.duplicate_ids:
            print(f"  {name} ({espn_id}): {' | '.join(entries)}")

    slots = [args.slot.upper()] if args.slot else ["QB", "RB", "WR", "TE", "K", "DL", "LB", "DB"]
    for slot in slots:
        top = top_by_slot(players, slot, args.limit)
        if not top:
            continue
        print(f"\n=== {slot} (top {len(top)}) ===")
        for i, p in enumerate(top, 1):
            print(f"  {i:>3}. {p.name:<26} {p.team:<4} {p.points:>7.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
