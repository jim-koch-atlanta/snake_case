"""Resolve keeper names from the league config to ESPN player ids.

Keepers are *known facts*, not picks anyone makes: the config says who is kept
and the schedule says which slot they occupy. They therefore belong in the
`DraftState` log from startup, seeded as `keeper`-source events.

Why this matters more than it looks. `app/serve.py` shows a MISSED counter that
compares picks entered against picks elapsed, and with manual entry the only
input path that counter is the sole guard against a dropped keystroke. A keeper
slot can never be typed in — it is not a live pick — so if keepers are not
seeded, the counter drifts to 36 over the draft and stops meaning anything.

An unresolvable keeper is a HARD ERROR (invariant #3). Leaving one unresolved is
worse than it sounds: the player would stay on the board as available all night,
because nothing would mark them drafted.
"""

from __future__ import annotations

import csv
import difflib
import json
from collections.abc import Sequence
from pathlib import Path

from engine.draft_state import KEEPER, PickEvent
from engine.schedule import Pick
from sources.build_crosswalk import normalize_name

ROOT = Path(__file__).resolve().parent.parent
CROSSWALK = ROOT / "data" / "crosswalk.csv"
ESPN_PLAYERS = ROOT / "data" / "espn" / "players.json"


class KeeperError(Exception):
    """Raised when a keeper cannot be resolved to an ESPN player id."""


def name_index(
    crosswalk: Path = CROSSWALK, espn_players: Path = ESPN_PLAYERS
) -> dict[str, int]:
    """normalized player name -> ESPN id.

    Built from the hand-reviewed crosswalk first, then topped up from the ESPN
    player universe, so a keeper who is not in any projection file still
    resolves.
    """
    index: dict[str, int] = {}
    if crosswalk.exists():
        with crosswalk.open(newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                if row.get("espn_id", "").isdigit():
                    index.setdefault(normalize_name(row["source_name"]), int(row["espn_id"]))
    if espn_players.exists():
        for entry in json.loads(espn_players.read_text()).get("players", []):
            p = entry.get("player", {})
            if p.get("fullName"):
                index.setdefault(normalize_name(p["fullName"]), int(p["id"]))
    return index


def _suggest(name: str, index: dict[str, int]) -> str:
    close = difflib.get_close_matches(normalize_name(name), list(index), n=1, cutoff=0.75)
    return f" Did you mean {close[0]!r} (espn_id {index[close[0]]})?" if close else ""


def keeper_events(
    schedule: Sequence[Pick], index: dict[str, int] | None = None
) -> list[PickEvent]:
    """One `keeper`-source PickEvent per keeper slot in the schedule.

    Raises KeeperError naming every unresolved keeper at once, with a spelling
    suggestion — a keeper typo in the config is the likely cause and it is
    faster to fix all of them in one pass.
    """
    index = name_index() if index is None else index
    events: list[PickEvent] = []
    unresolved: list[str] = []

    for pick in schedule:
        if pick.kind != "keeper":
            continue
        espn_id = index.get(normalize_name(pick.player or ""))
        if espn_id is None:
            unresolved.append(f"{pick.player!r} (overall {pick.overall}).{_suggest(pick.player or '', index)}")
            continue
        events.append(PickEvent(pick.overall, pick.team_id, espn_id, KEEPER))

    if unresolved:
        raise KeeperError(
            f"{len(unresolved)} keeper(s) in docs/league-config.toml could not be "
            "resolved to an ESPN player. They would stay on the board as available "
            "all draft. Fix the spelling in the config:\n  "
            + "\n  ".join(unresolved)
        )
    return events
