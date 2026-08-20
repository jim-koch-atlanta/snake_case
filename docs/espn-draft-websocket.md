# ESPN live draft WebSocket (reverse-engineered 2026-08-20)

The REST API (`lm-api-reads.fantasy.espn.com`) is **blind to an in-progress
draft** — verified at pick 67 of 264 across `mDraftDetail`, `mRoster`, `mTeam`,
`mMatchup`, `mStatus`, `mSettings`. Live picks arrive on a separate host over a
WebSocket.

Decoded from two browser HAR captures (155 and 608 frames). The second
included league-manager actions: pause, undo, assigning a pick to another team,
autodraft toggling, and queue edits.

## Endpoint

    wss://fantasydraft.espn.com/game-1/league-{LEAGUE_ID}/JOIN?<params>

| param | value | meaning |
|---|---|---|
| `1` | `1` | game id (ffl) |
| `2` | `{LEAGUE_ID}` | league |
| `3` | `{TEAM_ID}` | our team |
| `4` | `{SWID}` | SWID, braces included |
| `5` | `1:{LEAGUE_ID}:{TEAM_ID}:{SWID}:{MEMBER_NO}` | composite token |
| `6`,`7` | `false` | unknown flags |
| `8` | `KONA` | client id (same platform name as `kona_player_info`) |
| `nocache` | random int | cache buster |

**`MEMBER_NO` is stable, not a session token.** The same value appeared in two
different leagues (1113964546 and 1714522183) on different days for the same
SWID. That means the URL is constructible from `.env` — no DevTools copy-paste
needed on draft morning. Re-verify once before relying on it.

Auth is entirely in the URL. There is no login frame.

## Protocol

Space-delimited text. Client sends **only** keepalives:

    PING PING%20{epoch_ms}          every ~15s

`CLOCK` has three observed shapes — `CLOCK {n} {ms} {teamId}`, `CLOCK {n} {ms}`,
and a bare `CLOCK`. We ignore it entirely; any parser must not assume arity.

Server sends:

| frame | meaning |
|---|---|
| `INIT <base64>` | ~21KB of big-endian uint32 records; league/team ids visible. Structured binary, NOT a player list. Not needed — see below. |
| `TOKEN 1:{league}:{team}:{SWID}:{MEMBER_NO}` | echoes the composite token |
| `JOINED {teamId} {SWID}` | join ack |
| `AUTODRAFT {teamId} true` | autodraft state |
| `SELECTING {teamId} {clockMs}` | team is on the clock |
| `CLOCK {n} {remainingMs} {teamId}` | clock tick, ~5s apart |
| **`SELECTED {teamId} {playerId} {lineupSlotId} [{SWID}]`** | **the pick** — 4 OR 5 fields; the 5th is the SWID of the member who made it (our own SELECT, or an LM ASSIGN). Parse leniently. |
| `UNDONE {n}` | pick `n` was undone — **`n` is ZERO-BASED**, so `UNDONE 125` vacates overall pick **126** |
| `STATE {n}` | `1` = running, `3` = paused |
| `DRAFT_LIST {ids...}` | echo of the queue — the FULL list, not a delta |
| `AUTOSUGGEST {playerId}` | ESPN's suggestion for our team |
| `PONG {echo}` | keepalive reply |

`lineupSlotId` is the roster slot ESPN assigned the player to — flex slots accept
multiple positions (slot 3 took both RB and WR; slot 5 took WR and TE).

## The client can WRITE. We must not.

The browser client sends these, and so could we:

| frame | effect |
|---|---|
| `SELECT {playerId}` | **makes our pick, for real** |
| `ASSIGN {teamId} {playerId}` | LM: picks on behalf of another team |
| `UNDO {n}` | LM: undoes pick `n` (zero-based) |
| `PAUSE 1` | LM: pauses the draft |
| `AUTODRAFT true\|false` | toggles autodraft for our team |
| `DRAFT_LIST {ids...}` | replaces our queue (send the whole list) |

**Our listener must be structurally incapable of sending anything but `PING`.**
One stray `SELECT` during the real draft drafts a player. CLAUDE.md is explicit
that the tool proposes and the human decides; writing to this socket would
violate that in the most expensive way available. No `SELECT`/`ASSIGN`/`UNDO`
helper should exist in the codebase at all — not even unused.

## What this does and does not give us

Gives us: `(teamId, playerId)` per pick, in true draft order. Verified against
our spine — Jalen McMillan, Romeo Doubs, Tykee Smith, etc. all resolve.

Does NOT give us: the **overall pick number**. Derive it by walking a pointer
through our generated 264-slot schedule; each `SELECTED` advances to the next
live slot. That doubles as a correctness check — if the socket says team 13
picked where our schedule expects team 5, say so loudly. ESPN's own REST grid
does not reflect our 10 traded picks, so this is exactly where a mismatch would
surface.

## Open questions before relying on it

1. **Keepers.** The captures are from keeper-free mocks. Unknown whether keeper
   slots emit `SELECTED` or are simply absent. Changes the pointer arithmetic.
1b. **UNDO breaks naive pointer-walking.** An LM undo vacates a slot and the
   next `SELECTED` refills it. Our pointer must roll back on `UNDONE`, and
   `DraftState` needs a way to represent a vacated slot — it currently has no
   concept of removal, only override. See below.
2. **Reconnect mid-draft.** `INIT` is binary and undecoded, so a reconnect will
   not auto-recover missed picks. Manual entry fills the gap — which is why
   manual stays primary and this stays a convenience layer.
3. **Stability.** This is undocumented and can change without notice. It must
   never be load-bearing.

## Integration shape

Parse `SELECTED` → `PickEvent(overall, team_id, player_id, source=espn_sync)` →
`DraftState`. That path already exists and already loses to `manual`, so a wrong
or stale socket pick can always be overridden at the keyboard.

## Required DraftState change

`DraftState` is append-only with no notion of deletion, but `UNDONE` vacates a
slot. Do NOT delete events — that breaks replay. Instead append a vacate event
(e.g. `player_id = None` or a `VACATED` sentinel) and have `resolved()` treat
the winning event for a slot as "empty" when it is a vacate. Precedence stays
as-is, so a `manual` correction still beats an `espn_sync` vacate.

Remember the zero-based offset: `UNDONE 125` vacates **overall pick 126**.
Getting that wrong silently shifts every subsequent pick by one.
