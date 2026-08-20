"""Parse docs/league-config.toml (the canonical league facts) into engine inputs.

This is the I/O + validation layer (architecture invariant #2): engine/ stays
pure, and every loud-error-on-bad-config rule (invariant #3) lives here. The
keeper table defines the real pick schedule, so a missing/placeholder table is a
hard error, not a silent pure-snake fallback.

    uv run python -m sources.league_config      # prints schedule summary + my picks
"""

from __future__ import annotations

import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

from engine.schedule import (
    Keeper,
    TradedPick,
    build_pick_schedule,
    live_picks,
    team_live_picks,
)

CONFIG_PATH = Path(__file__).resolve().parent.parent / "docs" / "league-config.toml"
IR_SLOT = "IR"


class ConfigError(Exception):
    """Raised loudly when the league config is missing or inconsistent."""


@dataclass
class Team:
    slot: int  # 1-based draft position
    name: str
    team_id: int


@dataclass
class LeagueConfig:
    league_id: int
    season: int
    teams: list[Team]  # ordered by draft slot
    num_rounds: int
    keepers: list[Keeper]
    trades: list[TradedPick]
    my_team_id: int

    @property
    def draft_order(self) -> list[int]:
        return [t.team_id for t in self.teams]


def _get(data: object, *path: str) -> object:
    """Fetch a nested config key, naming the full dotted path if it is missing.

    Hand-edited config: a bare KeyError('team') gives no clue which section is
    wrong, so every required lookup goes through here (invariant #3 — fail
    loudly and legibly).
    """
    cur = data
    walked: list[str] = []
    for key in path:
        where = ".".join(walked) or "<top level>"
        if not isinstance(cur, dict):
            raise ConfigError(
                f"expected a table at '{where}' containing '{key}', "
                f"found {type(cur).__name__}"
            )
        if key not in cur:
            present = ", ".join(sorted(cur)) or "(nothing)"
            raise ConfigError(
                f"missing required key '{'.'.join([*walked, key])}' "
                f"— keys present under '{where}': {present}"
            )
        cur = cur[key]
        walked.append(key)
    return cur


def _row_get(row: object, key: str, where: str) -> object:
    """Fetch a key from one row of an array-of-tables, e.g. draft.keepers[3].

    `where` is the row's location, so a typo in row 17 of 36 says row 17.
    """
    if not isinstance(row, dict):
        raise ConfigError(
            f"{where}: expected a table like {{ {key} = ... }}, "
            f"found {type(row).__name__}"
        )
    if key not in row:
        present = ", ".join(sorted(row)) or "(nothing)"
        raise ConfigError(f"{where}: missing '{key}' — keys present: {present}")
    return row[key]


def resolve_team_name_to_id(team_name: str, teams: list[Team], where: str = "") -> int:
    name_to_id = {t.name.lower(): t.team_id for t in teams}
    ids = {t.team_id for t in teams}

    tid = name_to_id.get(str(team_name).lower())
    if tid is None and str(team_name).isdigit() and int(team_name) in ids:
        tid = int(team_name)
    if tid is None:
        known = ", ".join(t.name for t in teams)
        prefix = f"{where}: " if where else ""
        raise ConfigError(
            f"{prefix}unknown team '{team_name}' — must be one of: {known}"
        )
    return tid


def _parse_keepers(data: dict, teams: list[Team]) -> list[Keeper]:
    keepers: list[Keeper] = []

    for i, row in enumerate(_get(data, "draft", "keepers")):
        where = f"draft.keepers[{i}]"
        keepers.append(
            Keeper(
                team_id=resolve_team_name_to_id(
                    _row_get(row, "team", where), teams, where
                ),
                player=_row_get(row, "player", where),
                declared_round=int(_row_get(row, "round", where)),
            )
        )

    if not keepers:
        raise ConfigError(
            "draft.keepers is empty. Expected 36 rows (3 per team) — the pick "
            "schedule is undefined without it."
        )

    expected = 3 * len(teams)
    if len(keepers) != expected:
        counts: dict[int, int] = {}
        for k in keepers:
            counts[k.team_id] = counts.get(k.team_id, 0) + 1
        names = {t.team_id: t.name for t in teams}
        off = {names.get(tid, tid): c for tid, c in counts.items() if c != 3}
        missing = [t.name for t in teams if t.team_id not in counts]
        raise ConfigError(
            f"draft.keepers: expected {expected} keepers (3 per team), got "
            f"{len(keepers)}. Teams not at exactly 3: {off or 'none'}"
            + (f". Teams with no keepers at all: {missing}" if missing else "")
        )
    return keepers


def _parse_trades(data: dict, teams: list[Team]) -> list[TradedPick]:
    trades: list[TradedPick] = []
    for i, row in enumerate(_get(data, "draft", "traded_picks")):
        where = f"draft.traded_picks[{i}]"
        trades.append(
            TradedPick(
                from_team_id=resolve_team_name_to_id(
                    _row_get(row, "from_team", where), teams, where
                ),
                to_team_id=resolve_team_name_to_id(
                    _row_get(row, "to_team", where), teams, where
                ),
                round=int(_row_get(row, "round", where)),
            )
        )
    return trades


def load_league_config(path: Path = CONFIG_PATH) -> LeagueConfig:
    if not path.exists():
        raise FileNotFoundError(f"league config not found: {path}")
    with path.open(mode="rb") as f:
        try:
            data = tomllib.load(f)
        except tomllib.TOMLDecodeError as e:
            raise ConfigError(f"{path.name}: invalid TOML — {e}") from e

    try:
        teams = [
            Team(
                slot=_row_get(row, "slot", f"draft.order[{i}]"),
                name=_row_get(row, "name", f"draft.order[{i}]"),
                team_id=_row_get(row, "team_id", f"draft.order[{i}]"),
            )
            for i, row in enumerate(_get(data, "draft", "order"))
        ]
        return LeagueConfig(
            league_id=_get(data, "basics", "league_id"),
            season=_get(data, "basics", "season"),
            teams=teams,
            num_rounds=_get(data, "basics", "draft_rounds"),
            keepers=_parse_keepers(data, teams),
            trades=_parse_trades(data, teams),
            my_team_id=resolve_team_name_to_id(
                _get(data, "basics", "my_team"), teams, "basics.my_team"
            ),
        )
    except ConfigError as e:
        # one place adds the file context, so messages stay short at the raise site
        raise ConfigError(f"{path.name}: {e}") from None


def main() -> int:
    try:
        cfg = load_league_config()
        schedule = build_pick_schedule(
            cfg.draft_order, cfg.num_rounds, cfg.keepers, cfg.trades
        )
    except (ConfigError, FileNotFoundError, ValueError) as e:
        print(f"CONFIG ERROR: {e}", file=sys.stderr)
        return 1

    live = live_picks(schedule)
    print(
        f"League {cfg.league_id} season {cfg.season}: {len(cfg.teams)} teams, "
        f"{cfg.num_rounds} rounds -> {len(schedule)} slots"
    )
    print(
        f"Keepers: {len(schedule) - len(live)} | live picks: {len(live)} "
        f"| trades: {len(cfg.trades)}"
    )

    names = {t.team_id: t.name for t in cfg.teams}
    mine = team_live_picks(schedule, cfg.my_team_id)
    print(f"\nMy live picks (team_id {cfg.my_team_id}), {len(mine)} total:")
    for p in mine:
        note = f"  <- acquired from {names.get(p.original_team_id, p.original_team_id)}" if p.is_traded else ""
        print(
            f"  overall {p.overall:>3}  round {p.round:>2}  "
            f"(slot {p.pick_in_round} in round){note}"
        )

    lost = [
        p
        for p in live
        if p.is_traded and p.original_team_id == cfg.my_team_id
    ]
    if lost:
        print("\nMy original picks traded away:")
        for p in lost:
            print(
                f"  overall {p.overall:>3}  round {p.round:>2}  "
                f"-> {names.get(p.team_id, p.team_id)}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
