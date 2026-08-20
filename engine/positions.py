"""NFL position -> our roster slot.

Every source labels players with real NFL positions (DE/DT/CB/S/LB/...), but our
lineup is defined in terms of roster slots (see `[roster.slots]` in
docs/league-config.toml). The mapping is needed in at least three places —
the FantasyPros cleaner, the crosswalk builder, and anything that reads
data/historical/ — so it lives here rather than being redefined per caller.

Pure data + pure functions, no I/O (architecture invariant #1). Lives in
`engine/` rather than `sources/` because the scoring engine needs it too, and
engine/ must never import from sources/.

Slots here are *base* slots. Flex eligibility (a RB/WR slot accepting either an
RB or a WR, a WR/TE slot accepting either) is a lineup-feasibility concern and
is deliberately NOT modelled here.
"""

from __future__ import annotations

# Base roster slots. Note there is no D/ST slot in this league.
QB, RB, WR, TE, K = "QB", "RB", "WR", "TE", "K"
DL, LB, DB = "DL", "LB", "DB"

OFFENSE_SLOTS = frozenset({QB, RB, WR, TE})
IDP_SLOTS = frozenset({DL, LB, DB})
ALL_SLOTS = frozenset({QB, RB, WR, TE, K, DL, LB, DB})

# Positions actually observed in our data as of 2026-08-19:
#   data/historical/           WR RB TE QB K LB DE S DT CB
#   data/4for4/ (offense)      QB RB WR TE K
#   data/4for4/ (idp)          CB S DE DT LB
#   data/fantasypros/          LB DE S CB DT
POSITION_TO_SLOT: dict[str, str] = {
    # offense
    "QB": QB,
    "RB": RB,
    "WR": WR,
    "TE": TE,
    "K": K,
    # defensive line
    "DE": DL,
    "DT": DL,
    # linebacker
    "LB": LB,
    # defensive backs
    "CB": DB,
    "S": DB,
}

# Unambiguous variants not present in our current data, accepted defensively so
# a source that spells a position differently does not hard-fail mid-draft.
# Deliberately excluded because they are genuinely ambiguous or unsupported:
#   EDGE (DL or LB depending on scheme), DST/D/ST (no such slot in this league),
#   FB, OL, and anything else -> UnknownPositionError.
POSITION_ALIASES: dict[str, str] = {
    "PK": K,  # place kicker
    "NT": DL,  # nose tackle
    "DL": DL,  # already a slot name
    "MLB": LB,
    "ILB": LB,
    "OLB": LB,
    "FS": DB,  # free safety
    "SS": DB,  # strong safety
    "DB": DB,  # already a slot name
    "SAF": DB,
}


class UnknownPositionError(ValueError):
    """Raised when a position string has no roster-slot mapping.

    Loud by design (invariant #3): a silently dropped or mis-slotted player is
    a hole in the lineup we would not notice until draft day.
    """


def normalize_position(position: str) -> str:
    """Upper-case and strip a raw position string. Handles 'D/ST'-style slashes."""
    return str(position).strip().upper().replace("/", "").replace(".", "")


def slot_for_position(position: str) -> str:
    """Map an NFL position to its roster slot, raising on anything unknown."""
    key = normalize_position(position)
    if not key:
        raise UnknownPositionError("empty position string")
    if key in POSITION_TO_SLOT:
        return POSITION_TO_SLOT[key]
    if key in POSITION_ALIASES:
        return POSITION_ALIASES[key]
    known = ", ".join(sorted(set(POSITION_TO_SLOT) | set(POSITION_ALIASES)))
    raise UnknownPositionError(f"unknown position {position!r} — known: {known}")


def is_known_position(position: str) -> bool:
    """True if `position` maps to a roster slot."""
    try:
        slot_for_position(position)
    except UnknownPositionError:
        return False
    return True


def is_idp(position: str) -> bool:
    """True if `position` is a defensive (IDP) position."""
    return slot_for_position(position) in IDP_SLOTS
