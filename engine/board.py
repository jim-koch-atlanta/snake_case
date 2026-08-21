"""The draft board: what is left, what I have, and how long until I pick.

Pure functions over a `DraftState` and a valued player pool (invariant #1) —
`app/serve.py` does the HTTP and HTML, this does the thinking. Everything here
is a derived view, so the board is always a function of the pick log and never
a second copy of the truth that can drift.

The three numbers that matter live, in priority order:

  1. WHO IS LEFT, ranked by value over replacement
  2. WHETHER I HAVE ENTERED EVERY PICK — with manual entry the only input path,
     a missed pick silently corrupts the board. `DraftProgress.gap` surfaces it.
  3. HOW LONG UNTIL I AM UP — drives whether to think or to relax
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from engine.draft_state import DraftState, PickEvent
from engine.schedule import Pick
from engine.vor import ReplacementLevel, value_over_replacement


@dataclass(frozen=True)
class BoardPlayer:
    """A player as the board sees them: identity, value, availability."""

    espn_id: int
    name: str
    slot: str
    team: str
    points: float
    vor: float


@dataclass(frozen=True)
class DraftProgress:
    """Where the draft is, and whether our log has kept up with it."""

    entered: int  #: picks recorded in DraftState
    elapsed: int  #: picks the schedule says should have happened
    on_the_clock: int | None  #: team_id currently picking, if known
    my_next_overall: int | None  #: my next live pick
    picks_until_mine: int | None  #: how many picks until then

    @property
    def gap(self) -> int:
        """Picks the draft has made that we have not recorded. Must stay 0."""
        return max(0, self.elapsed - self.entered)

    @property
    def in_sync(self) -> bool:
        return self.gap == 0


def to_board_players(
    players: Iterable,
    levels: Mapping[str, ReplacementLevel],
) -> list[BoardPlayer]:
    """Attach VOR to a valued player pool and sort it, best first.

    Takes anything with `.espn_id/.name/.slot/.team/.points` so `engine/` never
    has to import `sources.projections.ValuedPlayer`.
    """
    rows = [
        BoardPlayer(
            espn_id=p.espn_id, name=p.name, slot=p.slot, team=getattr(p, "team", ""),
            points=p.points, vor=value_over_replacement(p.points, p.slot, levels),
        )
        for p in players
    ]
    rows.sort(key=lambda r: -r.vor)
    return rows


def draft_progress(
    schedule: Sequence[Pick],
    state: DraftState,
    my_team_id: int,
) -> DraftProgress:
    """Compare the pick log against the schedule.

    `elapsed` counts every slot at or before the highest overall pick we have
    recorded — keepers included, since they occupy real slots. If someone drafts
    and we do not type it, `gap` goes positive and stays there.
    """
    recorded = state.resolved()
    entered = len(recorded)
    highest = max(recorded, default=0)
    elapsed = sum(1 for p in schedule if p.overall <= highest)

    remaining = [p for p in schedule if p.overall > highest and p.kind == "live"]
    on_the_clock = remaining[0].team_id if remaining else None
    mine = next((p for p in remaining if p.team_id == my_team_id), None)
    picks_until = (
        sum(1 for p in remaining if p.overall < mine.overall) if mine else None
    )
    return DraftProgress(
        entered=entered,
        elapsed=elapsed,
        on_the_clock=on_the_clock,
        my_next_overall=mine.overall if mine else None,
        picks_until_mine=picks_until,
    )


def board_view(
    players: Sequence[BoardPlayer],
    state: DraftState,
    slot: str | None = None,
    query: str = "",
    limit: int = 50,
) -> list[BoardPlayer]:
    """The board as rendered: available only, optionally filtered, then capped."""
    rows = available_players(players, state.drafted_player_ids())
    if slot:
        rows = [r for r in rows if r.slot == slot.upper()]
    if query:
        rows = search_players(rows, query, limit=limit)
    return rows[:limit]


# ---------------------------------------------------------------------------
# TODO(jim): three helpers and one bigger piece — see tests/test_board.py
# ---------------------------------------------------------------------------

def available_players(
    players: Sequence[BoardPlayer], drafted_ids: set[int]
) -> list[BoardPlayer]:
    """Players not yet drafted, in the order they were given.

    `drafted_ids` comes from ``DraftState.drafted_player_ids()``. Preserve the
    input order — `to_board_players` already sorted by VOR and re-sorting here
    would throw that away.

    >>> available_players([a, b, c], {b.espn_id})
    [a, c]
    """
    return [p for p in players if p.espn_id not in drafted_ids]


def roster_by_slot(
    picks: Sequence[PickEvent], players_by_id: Mapping[int, BoardPlayer]
) -> dict[str, list[BoardPlayer]]:
    """Group my drafted players by roster slot.

    `picks` is a sequence of `PickEvent` (from ``DraftState.roster(team_id)``);
    `players_by_id` maps an ESPN id to the player. Returns
    ``{slot: [BoardPlayer, ...]}`` in pick order.

    A pick whose `player_id` is not in `players_by_id` is SKIPPED — that happens
    for keepers and for anyone outside the projection pool, and it must not
    crash the board mid-draft.

    >>> roster_by_slot([pick_wr, pick_wr2, pick_lb], by_id)
    {'WR': [chase, nacua], 'LB': [campbell]}
    """
    result: dict[str, list[BoardPlayer]] = {}
    for pick in picks:
        player = players_by_id.get(pick.player_id)
        if player is not None:
            result.setdefault(player.slot, []).append(player)
    return result


def picks_until(schedule: Sequence[Pick], my_team_id: int, after_overall: int) -> int | None:
    """How many picks happen before my next live pick, after `after_overall`.

    Counts LIVE picks only — keeper slots are pre-assigned and nobody waits on
    them. Returns None if I have no live picks left.

    With my next pick at overall 30 and picks 27, 28, 29 live in between, the
    answer is 3.

    >>> picks_until(schedule, my_team_id=14, after_overall=6)
    23
    """
    picks: int = 0
    for pick in schedule:
        if pick.overall > after_overall and pick.kind != "keeper":
            if pick.team_id == my_team_id:
                return picks
            picks = picks + 1

    return None


def search_players(
    players: Sequence[BoardPlayer], query: str, limit: int = 10
) -> list[BoardPlayer]:
    """Name search for the pick-entry box. The bigger piece.

    Under a 90-second clock you type a few characters and need the right player
    first. Required behaviour, in order of importance:

    1. **Case-insensitive.** ``"chase"`` finds ``"Ja'Marr Chase"``.
    2. **Matches any word**, not just the start of the full name — ``"chase"``
       must match on the surname.
    3. **Punctuation- AND space-insensitive.** ``"jamarr"`` finds ``"Ja'Marr"``,
       ``"amonra"`` finds ``"Amon-Ra"``, and ``"st brown"`` finds
       ``"St. Brown"``. Two normalized forms make this easy:

         - a *worded* form: lowercase, drop ``'`` ``.``, turn ``-`` into a
           space. Used for the word-prefix check in rule 4.
         - a *squashed* form: the worded form with ALL spaces removed. Used for
           the "contains" check, so ``"amonra"`` matches ``"amon ra ..."``.

       Normalize the query the same way you normalize the name.
    4. **Rank exact-ish matches first.** A player whose name STARTS with the
       query outranks one that merely contains it, so ``"ja"`` puts
       ``"Ja'Marr Chase"`` above ``"Puka Nacua"``... but among equally good
       matches, keep the incoming VOR order.
    5. **Empty query returns everything** (the caller slices), and `limit` caps
       the result.

    >>> [p.name for p in search_players(pool, "chase")]
    ["Ja'Marr Chase"]
    """
    output_startswith_worded: list[BoardPlayer] = []
    output_startswith_squashed: list[BoardPlayer] = []
    output_contains_worded: list[BoardPlayer] = []
    output_contains_squashed: list[BoardPlayer] = []

    def normalize(word: str) -> tuple[str, str]:
        worded = word.lower().replace("'", " ").replace(".", " ").replace("-", " ")
        squashed = worded.replace(" ", "")
        return (worded, squashed)

    (query_worded, query_squashed) = normalize(query) 

    if len(query_squashed) == 0:
        return players[:limit]
    
    for player in players:
        (worded, squashed) = normalize(player.name)
        if any(w.startswith(query_worded) for w in worded.split()):
            output_startswith_worded.append(player)
        elif squashed.startswith(query_squashed):
            output_startswith_squashed.append(player)
        elif worded.find(query_worded) != -1:
            output_contains_worded.append(player)
        elif squashed.find(query_squashed) != -1:
            output_contains_squashed.append(player)

        if len(output_startswith_worded) == limit:
            break

    output = [
        *output_startswith_worded,
        *output_startswith_squashed,
        *output_contains_worded,
        *output_contains_squashed,
    ][:limit]
    return output