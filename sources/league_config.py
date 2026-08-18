"""Parse docs/league-config.md (the canonical league facts) into engine inputs.

This is the I/O + validation layer (architecture invariant #2): engine/ stays
pure, and every loud-error-on-bad-config rule (invariant #3) lives here. The
keeper table defines the real pick schedule, so a missing/placeholder table is a
hard error, not a silent pure-snake fallback.

    uv run python -m sources.league_config      # prints schedule summary + my picks
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

from engine.schedule import (
    Keeper,
    build_pick_schedule,
    live_picks,
    team_live_picks,
)

CONFIG_PATH = Path(__file__).resolve().parent.parent / "docs" / "league-config.md"
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
    my_team_id: int

    @property
    def draft_order(self) -> list[int]:
        return [t.team_id for t in self.teams]


def _section(text: str, header_prefix: str) -> list[str]:
    """Lines of the first '## <header_prefix>...' section, until the next '## '."""
    out: list[str] = []
    capturing = False
    for line in text.splitlines():
        if line.startswith("## "):
            if capturing:
                break
            capturing = line[3:].strip().lower().startswith(header_prefix.lower())
            continue
        if capturing:
            out.append(line)
    return out


def _find_int(text: str, pattern: str, label: str) -> int:
    m = re.search(pattern, text)
    if not m:
        raise ConfigError(f"could not find {label} in league config")
    return int(m.group(1))


def _parse_draft_order(text: str) -> list[Team]:
    teams: list[Team] = []
    for line in _section(text, "draft order"):
        s = line.replace("`", "").strip()
        m = re.match(r"^(\d+)\.\s*(.+?)\s*\(ESPN team_id:\s*(\d+)\)\s*$", s)
        if m:
            teams.append(
                Team(slot=int(m.group(1)), name=m.group(2).strip(), team_id=int(m.group(3)))
            )
    if not teams:
        raise ConfigError("no draft-order rows parsed from '## Draft order'")
    ids = [t.team_id for t in teams]
    if len(set(ids)) != len(ids):
        raise ConfigError(f"duplicate team_ids in draft order: {ids}")
    return teams


def _parse_my_team(text: str, teams: list[Team]) -> int:
    m = re.search(r"My team:\s*(.+?)\s*\(position", text.replace("`", ""))
    if not m:
        raise ConfigError("could not find 'My team:' line")
    name = m.group(1).strip()
    for t in teams:
        if t.name.lower() == name.lower():
            return t.team_id
    raise ConfigError(f"my team '{name}' not found in draft order")


def _parse_num_rounds(text: str) -> int:
    total = 0
    found = False
    for line in _section(text, "roster"):
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 2 and cells[1].isdigit():
            slot, count = cells[0], int(cells[1])
            if slot.upper() != IR_SLOT:  # IR is not drafted
                total += count
                found = True
    if not found:
        raise ConfigError("no roster rows parsed from '## Roster'")
    return total


def _parse_keepers(text: str, teams: list[Team]) -> list[Keeper]:
    name_to_id = {t.name.lower(): t.team_id for t in teams}
    ids = {t.team_id for t in teams}
    keepers: list[Keeper] = []
    placeholders = 0

    for line in _section(text, "keepers"):
        if "|" not in line:
            continue
        cells = [c.strip().strip("`").strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 3:
            continue
        team_s, player_s, round_s = cells
        if team_s.lower() == "team":  # header row
            continue
        if set(team_s) <= set("-: "):  # separator row
            continue
        if any("TODO" in c.upper() for c in cells) or not round_s.isdigit():
            placeholders += 1
            continue
        tid = name_to_id.get(team_s.lower())
        if tid is None and team_s.isdigit() and int(team_s) in ids:
            tid = int(team_s)
        if tid is None:
            raise ConfigError(f"keeper row references unknown team '{team_s}'")
        keepers.append(Keeper(team_id=tid, player=player_s, declared_round=int(round_s)))

    if not keepers:
        raise ConfigError(
            f"'## Keepers' table is not filled in ({placeholders} placeholder/TODO "
            "row(s), 0 real keepers). Expected 36 rows (3 per team). Fill it in "
            "docs/league-config.md — the pick schedule is undefined without it."
        )

    expected = 3 * len(teams)
    if len(keepers) != expected:
        counts: dict[int, int] = {}
        for k in keepers:
            counts[k.team_id] = counts.get(k.team_id, 0) + 1
        off = {tid: c for tid, c in counts.items() if c != 3}
        raise ConfigError(
            f"expected {expected} keepers (3 per team), parsed {len(keepers)}. "
            f"Teams not at exactly 3: {off or 'n/a (some team missing entirely)'}"
        )
    return keepers


def load_league_config(path: Path = CONFIG_PATH) -> LeagueConfig:
    if not path.exists():
        raise FileNotFoundError(f"league config not found: {path}")
    text = path.read_text()
    teams = _parse_draft_order(text)
    return LeagueConfig(
        league_id=_find_int(text, r"League ID:\s*`?(\d+)`?", "League ID"),
        season=_find_int(text, r"Season:\s*`?(\d+)`?", "Season"),
        teams=teams,
        num_rounds=_parse_num_rounds(text),
        keepers=_parse_keepers(text, teams),
        my_team_id=_parse_my_team(text, teams),
    )


def main() -> int:
    try:
        cfg = load_league_config()
        schedule = build_pick_schedule(cfg.draft_order, cfg.num_rounds, cfg.keepers)
    except (ConfigError, FileNotFoundError, ValueError) as e:
        print(f"CONFIG ERROR: {e}", file=sys.stderr)
        return 1

    live = live_picks(schedule)
    print(
        f"League {cfg.league_id} season {cfg.season}: {len(cfg.teams)} teams, "
        f"{cfg.num_rounds} rounds -> {len(schedule)} slots"
    )
    print(f"Keepers: {len(schedule) - len(live)} | live picks: {len(live)}")

    mine = team_live_picks(schedule, cfg.my_team_id)
    print(f"\nMy live picks (team_id {cfg.my_team_id}), {len(mine)} total:")
    for p in mine:
        print(f"  overall {p.overall:>3}  round {p.round:>2}  (slot {p.pick_in_round} in round)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
