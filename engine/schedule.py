"""Pick-schedule generator: the snake-with-holes.

Priority #1 in CLAUDE.md — everything downstream (survival model, feasibility
guard, "my next live pick") keys off which overall picks are keeper slots vs.
live picks. Pure functions, zero I/O (architecture invariant #1). Turning
docs/league-config.md into the inputs here lives in sources/league_config.py.

Model: a snake draft is a fixed grid of `num_rounds x num_teams` slots, one slot
per team per round. A keeper "kept in round X" consumes that team's round-X
slot (it becomes a keeper pick, not a live pick). Same-round collisions cascade
to earlier rounds — see resolve_keeper_rounds.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass


@dataclass(frozen=True)
class Keeper:
    """One kept player, declared in the round they were originally drafted."""

    team_id: int
    player: str
    declared_round: int


@dataclass(frozen=True)
class Pick:
    """One slot in the draft grid.

    `round` is where the pick actually sits. For keepers, `declared_round` is the
    round the player was declared kept in — it equals `round` unless a same-round
    collision shifted the slot earlier.
    """

    overall: int  # 1-based, 1..num_rounds*num_teams
    round: int  # 1-based
    pick_in_round: int  # 1-based slot within the round, 1..num_teams
    team_id: int
    kind: str  # "keeper" | "live"
    player: str | None = None  # set for keepers
    declared_round: int | None = None  # set for keepers


def team_at_slot(draft_order: list[int], round_: int, pick_in_round: int) -> int:
    """Which team owns (round, pick_in_round) under snake ordering.

    Odd rounds run in draft order; even rounds reverse it.
    """
    n = len(draft_order)
    if round_ % 2 == 1:
        return draft_order[pick_in_round - 1]
    return draft_order[n - pick_in_round]


def resolve_keeper_rounds(declared_rounds: list[int], num_rounds: int) -> list[int]:
    """Resolve same-round keeper collisions for ONE team.

    Each keeper prefers its declared round; if that round is already taken, it
    shifts one round *earlier*, cascading (three keepers declared in round X land
    on X, X-1, X-2). Returns actual rounds parallel to `declared_rounds`.

    The *set* of resulting rounds depends only on the multiset of declared rounds,
    not on processing order (fixed-direction linear probing), so the live-pick
    schedule is well-defined. The keeper->round labeling uses a deterministic
    tie-break (declared round desc, then input order); it never affects which
    overall picks are live, only which player is shown at which keeper slot.

    Raises ValueError on out-of-range rounds or a cascade below round 1.
    """
    for r in declared_rounds:
        if not (1 <= r <= num_rounds):
            raise ValueError(f"keeper declared_round {r} outside 1..{num_rounds}")

    order = sorted(range(len(declared_rounds)), key=lambda i: (-declared_rounds[i], i))
    occupied: set[int] = set()
    actual = [0] * len(declared_rounds)
    for i in order:
        r = declared_rounds[i]
        while r in occupied:
            r -= 1
        if r < 1:
            raise ValueError(
                "keeper round collision cascaded below round 1 for a team with "
                f"declared rounds {sorted(declared_rounds)}; no legal earlier slot"
            )
        occupied.add(r)
        actual[i] = r
    return actual


def build_pick_schedule(
    draft_order: list[int],
    num_rounds: int,
    keepers: list[Keeper],
) -> list[Pick]:
    """Build the full ordered pick schedule (all slots, keeper + live).

    `draft_order` is team_ids in round-1 order. Result is ordered by overall pick.
    """
    if not draft_order:
        raise ValueError("draft_order is empty")
    if len(set(draft_order)) != len(draft_order):
        raise ValueError("draft_order has duplicate team_ids")
    if num_rounds < 1:
        raise ValueError("num_rounds must be >= 1")

    team_set = set(draft_order)
    by_team: dict[int, list[Keeper]] = defaultdict(list)
    for k in keepers:
        if k.team_id not in team_set:
            raise ValueError(f"keeper team_id {k.team_id} not in draft_order")
        by_team[k.team_id].append(k)

    keeper_slot: dict[tuple[int, int], Keeper] = {}
    for team_id, team_keepers in by_team.items():
        actual = resolve_keeper_rounds(
            [k.declared_round for k in team_keepers], num_rounds
        )
        for k, r in zip(team_keepers, actual):
            keeper_slot[(team_id, r)] = k

    n = len(draft_order)
    schedule: list[Pick] = []
    for round_ in range(1, num_rounds + 1):
        for pir in range(1, n + 1):
            team_id = team_at_slot(draft_order, round_, pir)
            overall = (round_ - 1) * n + pir
            k = keeper_slot.get((team_id, round_))
            if k is not None:
                schedule.append(
                    Pick(
                        overall=overall,
                        round=round_,
                        pick_in_round=pir,
                        team_id=team_id,
                        kind="keeper",
                        player=k.player,
                        declared_round=k.declared_round,
                    )
                )
            else:
                schedule.append(
                    Pick(
                        overall=overall,
                        round=round_,
                        pick_in_round=pir,
                        team_id=team_id,
                        kind="live",
                    )
                )
    return schedule


def live_picks(schedule: list[Pick]) -> list[Pick]:
    """All live (non-keeper) picks, in overall order."""
    return [p for p in schedule if p.kind == "live"]


def team_live_picks(schedule: list[Pick], team_id: int) -> list[Pick]:
    """A team's live picks, in overall order."""
    return [p for p in schedule if p.kind == "live" and p.team_id == team_id]


def next_live_pick_after(
    schedule: list[Pick], overall: int, team_id: int
) -> Pick | None:
    """The team's next live pick strictly after `overall`, or None if none remain."""
    for p in schedule:
        if p.overall > overall and p.kind == "live" and p.team_id == team_id:
            return p
    return None
