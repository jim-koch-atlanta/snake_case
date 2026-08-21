"""Local draft-day UI. One page, no build step, stdlib only.

    uv run python -m app.serve            # http://localhost:8000

Manual pick entry is the ONLY input path into the draft (ESPN's read API is
blind to a live draft — see docs/decisions.md), so this page is the thing that
has to work on Friday. It is deliberately boring: `http.server` from the
standard library, vanilla fetch polling, no framework, no dependencies, no
build step (CLAUDE.md invariant #5).

Everything it displays is derived from the append-only `DraftState` log by
`engine/board.py`. The server holds one in-memory session; restarting loses the
log, so the log is also mirrored to disk after every write.
"""

from __future__ import annotations

import json
import sys
import tomllib
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from engine.board import (
    board_view,
    draft_progress,
    high_water_mark,
    picks_until,
    roster_by_slot,
    to_board_players,
)
from engine.draft_state import MANUAL, DraftState
from engine.schedule import build_pick_schedule
from engine.vor import pool_from_points, replacement_levels
from sources.keepers import keeper_events
from sources.league_config import load_league_config
from sources.projections import load_projections

ROOT = Path(__file__).resolve().parent.parent
PAGE = Path(__file__).resolve().parent / "index.html"
PICK_LOG = ROOT / "data" / "draft_log.json"


@dataclass
class Session:
    """Everything the page needs, loaded once at startup."""

    cfg: object
    schedule: list
    players: list
    levels: dict
    state: DraftState

    @property
    def by_id(self) -> dict:
        return {p.espn_id: p for p in self.players}


def build_session() -> Session:
    cfg = load_league_config()
    schedule = build_pick_schedule(cfg.draft_order, cfg.num_rounds, cfg.keepers, cfg.trades)
    valued, _report = load_projections()
    roster = tomllib.loads((ROOT / "docs" / "league-config.toml").read_text())["roster"]["slots"]
    levels = replacement_levels(
        pool_from_points([(p.slot, p.points) for p in valued]), roster, len(cfg.teams)
    )
    session = Session(cfg=cfg, schedule=schedule,
                      players=to_board_players(valued, levels), levels=levels,
                      state=DraftState())

    # Keepers are known facts, not picks anyone types. Seed them first so the
    # MISSED counter compares like with like -- without this it drifts to 36.
    seeded = keeper_events(schedule)
    session.state.extend(seeded)
    print(f"seeded {len(seeded)} keepers from the config")

    _restore(session)
    return session


def _restore(session: Session) -> None:
    """Replay a mirrored log, so a server restart mid-draft is survivable."""
    if not PICK_LOG.exists():
        return
    for e in json.loads(PICK_LOG.read_text()):
        if e["source"] == "keeper":
            continue  # already seeded from the config, which is authoritative
        session.state.record(e["overall_pick"], e["team_id"], e["player_id"], e["source"])
    print(f"restored {len(session.state.events)} pick event(s) from {PICK_LOG.name}")


def _persist(session: Session) -> None:
    PICK_LOG.parent.mkdir(parents=True, exist_ok=True)
    PICK_LOG.write_text(json.dumps([
        {"overall_pick": e.overall_pick, "team_id": e.team_id,
         "player_id": e.player_id, "source": e.source}
        for e in session.state.events if e.source != "keeper"
    ], indent=2))


def state_payload(session: Session) -> dict:
    """Counters, my roster, and who is on the clock."""
    my_id = session.cfg.my_team_id
    progress = draft_progress(session.schedule, session.state, my_id)
    names = {t.team_id: t.name for t in session.cfg.teams}
    roster = roster_by_slot(session.state.roster(my_id), session.by_id)
    highest = high_water_mark(session.state)
    return {
        "entered": progress.entered,
        "elapsed": progress.elapsed,
        "gap": progress.gap,
        "in_sync": progress.in_sync,
        "on_the_clock": names.get(progress.on_the_clock, progress.on_the_clock),
        "my_next_overall": progress.my_next_overall,
        "picks_until_mine": picks_until(session.schedule, my_id, highest),
        "my_team": names.get(my_id, my_id),
        "roster": {
            slot: [{"name": p.name, "vor": round(p.vor, 1)} for p in players]
            for slot, players in sorted(roster.items())
        },
        "conflicts": [c.detail for c in session.state.conflicts()],
        "recent": [
            {"overall": e.overall_pick, "team": names.get(e.team_id, e.team_id),
             "player": (session.by_id.get(e.player_id).name
                        if session.by_id.get(e.player_id) else str(e.player_id)),
             "source": e.source}
            # only picks the draft has actually reached — keepers are seeded
            # across all 22 rounds, so an unfiltered tail shows round-22 keepers
            for e in [x for x in session.state.picks() if x.overall_pick <= highest][-8:]
        ],
    }


def board_payload(session: Session, query: str, slot: str | None, limit: int) -> dict:
    rows = board_view(session.players, session.state, slot=slot, query=query, limit=limit)
    return {"players": [
        {"espn_id": r.espn_id, "name": r.name, "slot": r.slot, "team": r.team,
         "points": round(r.points, 1), "vor": round(r.vor, 1)}
        for r in rows
    ]}


def next_open_slot(session: Session, team_id: int) -> int | None:
    """The next unrecorded slot for a team — where a manual pick lands."""
    taken = set(session.state.resolved())
    for p in session.schedule:
        if p.team_id == team_id and p.overall not in taken and p.kind == "live":
            return p.overall
    return None


class Handler(BaseHTTPRequestHandler):
    session: Session  # set on the class before serving

    def log_message(self, *args) -> None:  # quiet; the draft is noisy enough
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: dict, code: int = 200) -> None:
        self._send(code, json.dumps(payload).encode(), "application/json")

    def do_GET(self) -> None:
        url = urlparse(self.path)
        params = parse_qs(url.query)
        if url.path == "/":
            self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        elif url.path == "/api/board":
            self._json(board_payload(
                self.session,
                query=params.get("q", [""])[0],
                slot=(params.get("slot", [""])[0] or None),
                limit=int(params.get("limit", ["50"])[0]),
            ))
        elif url.path == "/api/state":
            self._json(state_payload(self.session))
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        url = urlparse(self.path)
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        session = self.session

        if url.path == "/api/pick":
            player_id = int(body["player_id"])
            team_id = int(body.get("team_id") or 0)
            if not team_id:
                progress = draft_progress(session.schedule, session.state, session.cfg.my_team_id)
                team_id = progress.on_the_clock
            if team_id is None:
                self._json({"error": "draft is complete"}, 400)
                return
            overall = body.get("overall") or next_open_slot(session, team_id)
            if overall is None:
                self._json({"error": f"no open slot for team {team_id}"}, 400)
                return
            session.state.record(int(overall), team_id, player_id, MANUAL)
            _persist(session)
            self._json({"ok": True, "overall": overall, "team_id": team_id})
        elif url.path == "/api/undo":
            if session.state.events:
                session.state.events.pop()
                _persist(session)
            self._json({"ok": True, "events": len(session.state.events)})
        else:
            self._json({"error": "not found"}, 404)


def main() -> int:
    try:
        session = build_session()
    except Exception as e:  # noqa: BLE001 - startup must explain itself, not traceback
        print(f"STARTUP FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    Handler.session = session
    live = sum(1 for p in session.schedule if p.kind == "live")
    print(f"draft copilot: {len(session.players)} players, {live} live picks, "
          f"my team = {session.cfg.my_team_id}")
    print("serving on http://localhost:8000  (Ctrl-C to stop)")
    try:
        ThreadingHTTPServer(("127.0.0.1", 8000), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
