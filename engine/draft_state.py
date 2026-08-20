"""DraftState: an append-only event log of picks, with state derived by replay.

Architecture invariant #4. Nothing here mutates a pick in place — every
correction is a new event appended to the log, and the current board is a pure
function of that log. That makes the live path debuggable (the log is the whole
truth), replayable (mock drafts through `tools/mock_replay.py`), and safe to
reconcile against a flaky ESPN feed.

Reconciliation, when two sources report the same `overall_pick`:

    manual  >  keeper  >  espn_sync

  * manual > espn_sync is CLAUDE.md invariant #4 — the human at the keyboard is
    always right, and ESPN sync is a convenience layer that may fail.
  * keeper > espn_sync because docs/league-config.toml is authoritative for
    keepers; ESPN's `reservedForKeeper` flags disagree with it (verified
    2026-08-19) and are not final until declarations close.
  * manual > keeper so a live correction can override a stale config keeper
    without a config edit mid-draft.

Within a single source, the later event wins: appending again is how you fix a
typo. Pure, zero I/O (invariant #1).
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field, replace

KEEPER = "keeper"
MANUAL = "manual"
ESPN_SYNC = "espn_sync"

#: Higher wins when two events land on the same overall_pick.
SOURCE_PRECEDENCE: dict[str, int] = {ESPN_SYNC: 1, KEEPER: 2, MANUAL: 3}


class DraftStateError(ValueError):
    """Raised on a malformed pick event — a programming error, not a live condition."""


@dataclass(frozen=True)
class PickEvent:
    """One reported pick. `seq` is assigned on append and orders same-source events."""

    overall_pick: int
    team_id: int
    player_id: int
    source: str
    seq: int = -1

    def __post_init__(self) -> None:
        if self.overall_pick < 1:
            raise DraftStateError(f"overall_pick must be >= 1, got {self.overall_pick}")
        if self.source not in SOURCE_PRECEDENCE:
            raise DraftStateError(
                f"unknown source {self.source!r} — "
                f"expected one of {sorted(SOURCE_PRECEDENCE)}"
            )


@dataclass(frozen=True)
class Conflict:
    """An integrity problem found during replay. Reported, never raised.

    The tool must stay up during a live draft, so a bad feed degrades into a
    visible warning rather than taking the board down.
    """

    kind: str
    detail: str
    overall_picks: tuple[int, ...]
    player_id: int | None = None


@dataclass
class DraftState:
    """Append-only log of pick events. All reads are derived by replay."""

    events: list[PickEvent] = field(default_factory=list)

    # -- writing ----------------------------------------------------------

    def append(self, event: PickEvent) -> PickEvent:
        """Append one event, stamping it with the next sequence number."""
        stamped = replace(event, seq=len(self.events))
        self.events.append(stamped)
        return stamped

    def record(self, overall_pick: int, team_id: int, player_id: int, source: str) -> PickEvent:
        """Convenience wrapper around `append`."""
        return self.append(PickEvent(overall_pick, team_id, player_id, source))

    def extend(self, events: Iterable[PickEvent]) -> None:
        for e in events:
            self.append(e)

    # -- replay -----------------------------------------------------------

    def resolved(self) -> dict[int, PickEvent]:
        """The winning event per overall_pick, derived by replaying the log."""
        out: dict[int, PickEvent] = {}
        for e in self.events:
            cur = out.get(e.overall_pick)
            if cur is None or _wins(e, cur):
                out[e.overall_pick] = e
        return out

    def pick_at(self, overall_pick: int) -> PickEvent | None:
        return self.resolved().get(overall_pick)

    def picks(self) -> list[PickEvent]:
        """Resolved picks ordered by overall_pick."""
        return [self.resolved()[k] for k in sorted(self.resolved())]

    def drafted_player_ids(self) -> set[int]:
        return {e.player_id for e in self.resolved().values()}

    def is_drafted(self, player_id: int) -> bool:
        return player_id in self.drafted_player_ids()

    def roster(self, team_id: int) -> list[PickEvent]:
        """A team's picks so far, in draft order."""
        return [e for e in self.picks() if e.team_id == team_id]

    def team_player_ids(self, team_id: int) -> set[int]:
        return {e.player_id for e in self.roster(team_id)}

    def count(self) -> int:
        """Number of filled slots (resolved picks), not raw events."""
        return len(self.resolved())

    def __iter__(self) -> Iterator[PickEvent]:
        return iter(self.picks())

    def __len__(self) -> int:
        return self.count()

    # -- integrity --------------------------------------------------------

    def conflicts(self) -> list[Conflict]:
        """Integrity problems in the resolved board.

        Currently: the same player resolved into more than one slot. Reported
        rather than raised so a bad ESPN feed cannot take the tool down live.
        """
        out: list[Conflict] = []
        by_player: dict[int, list[int]] = {}
        for pick, e in self.resolved().items():
            by_player.setdefault(e.player_id, []).append(pick)
        for player_id, picks in sorted(by_player.items()):
            if len(picks) > 1:
                out.append(Conflict(
                    kind="duplicate_player",
                    detail=f"player {player_id} resolved into {len(picks)} slots",
                    overall_picks=tuple(sorted(picks)),
                    player_id=player_id,
                ))
        return out

    def overridden(self) -> list[tuple[PickEvent, PickEvent]]:
        """(losing, winning) pairs where a later/stronger event displaced another.

        Useful for surfacing "ESPN said X here, you said Y" in the UI.
        """
        winners = self.resolved()
        return [
            (e, winners[e.overall_pick])
            for e in self.events
            if winners[e.overall_pick] is not e
        ]


def _wins(candidate: PickEvent, incumbent: PickEvent) -> bool:
    """True if `candidate` should replace `incumbent` at the same overall_pick."""
    cp = SOURCE_PRECEDENCE[candidate.source]
    ip = SOURCE_PRECEDENCE[incumbent.source]
    if cp != ip:
        return cp > ip
    return candidate.seq > incumbent.seq  # same source: later corrects earlier


def keeper_events(keeper_picks: Iterable[tuple[int, int, int]]) -> list[PickEvent]:
    """Build keeper events from (overall_pick, team_id, player_id) triples.

    Kept separate from the pick-schedule generator so `engine/schedule.py` stays
    focused on slot ownership and this module stays focused on the event log.
    """
    return [PickEvent(o, t, p, KEEPER) for o, t, p in keeper_picks]
