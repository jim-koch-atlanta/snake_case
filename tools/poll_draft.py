#!/usr/bin/env python3
"""THROWAWAY experiment #1: does ESPN's mDraftDetail update live during a draft?

CLAUDE.md flags this as UNVERIFIED and priority #6. Point it at a mock draft
tonight and watch whether picks appear, and at what latency. If they do, sync is
worth wiring in; if not, manual entry stays the primary path.

Deliberately standalone: stdlib only (no requests / no dotenv), reads .env
itself, and is NOT imported by engine/ or the app. Safe to delete after the
experiment.

Usage:
    python3 tools/poll_draft.py                # poll every 5s using .env
    python3 tools/poll_draft.py --interval 3
    python3 tools/poll_draft.py --once         # one fetch, dump structure, exit
    python3 tools/poll_draft.py --dump         # also save raw JSON per poll to data/draft_snapshots/

Notes:
  - Prints playerId (not name) — name resolution needs the frozen crosswalk,
    out of scope for an experiment. teamId + overall + timestamp are enough to
    judge liveness/latency.
  - On the first fetch it prints the draftDetail keys and one raw pick so we can
    confirm field names (e.g. whether picks carry a `keeper` flag).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = (
    "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/"
    "seasons/{season}/segments/0/leagues/{league_id}"
)
ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
SNAP_DIR = ROOT / "data" / "draft_snapshots"


def load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if path.exists():
        for raw in path.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip()
    for key in ("SWID", "ESPN_S2", "LEAGUE_ID", "SEASON"):  # real env overrides .env
        if os.environ.get(key):
            env[key] = os.environ[key]
    return env


def ts() -> str:
    return time.strftime("%H:%M:%S", time.localtime())


def fetch(url: str, cookie: str) -> dict:
    req = urllib.request.Request(
        url,
        headers={
            "Cookie": cookie,
            "User-Agent": "Mozilla/5.0 (draft-copilot poller)",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def describe_pick(p: dict) -> str:
    return (
        f"PICK #{p.get('overallPickNumber'):>3} "
        f"R{p.get('roundId')}.{p.get('roundPickNumber')} "
        f"team={p.get('teamId')} player={p.get('playerId')} "
        f"keeper={p.get('keeper')} auto={p.get('autoDraftTypeId')}"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--interval", type=float, default=5.0, help="seconds between polls")
    ap.add_argument("--once", action="store_true", help="single fetch then exit")
    ap.add_argument("--dump", action="store_true", help="save raw JSON per poll")
    ap.add_argument("--view", default="mDraftDetail")
    args = ap.parse_args()

    env = load_env(ENV_PATH)
    missing = [k for k in ("SWID", "ESPN_S2", "LEAGUE_ID") if not env.get(k)]
    if missing:
        print(
            f"Missing env vars {missing} (looked in {ENV_PATH} and os.environ).",
            file=sys.stderr,
        )
        return 2

    season = env.get("SEASON", "2026")
    league_id = env["LEAGUE_ID"]
    url = BASE.format(season=season, league_id=league_id) + f"?view={args.view}"
    cookie = f"SWID={env['SWID']}; espn_s2={env['ESPN_S2']}"

    print(f"[{ts()}] polling {url}")
    print(f"[{ts()}] league={league_id} season={season} interval={args.interval}s (Ctrl-C to stop)")
    if args.dump:
        SNAP_DIR.mkdir(parents=True, exist_ok=True)

    seen: set = set()
    last_status = None
    first = True
    poll_no = 0
    while True:
        poll_no += 1
        try:
            data = fetch(url, cookie)
        except urllib.error.HTTPError as e:
            body = e.read()[:300].decode("utf-8", "replace")
            if e.code in (401, 403):
                print(
                    f"[{ts()}] AUTH {e.code}: cookies rejected/expired — refresh "
                    f"SWID/espn_s2 in .env. {body}",
                    file=sys.stderr,
                )
            else:
                print(f"[{ts()}] HTTP {e.code}: {body}", file=sys.stderr)
            if args.once:
                return 1
            time.sleep(args.interval)
            continue
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            print(f"[{ts()}] fetch error: {e}", file=sys.stderr)
            if args.once:
                return 1
            time.sleep(args.interval)
            continue

        detail = data.get("draftDetail", {}) or {}
        picks = detail.get("picks", []) or []
        status = (detail.get("inProgress"), detail.get("drafted"), len(picks))

        if args.dump:
            (SNAP_DIR / f"poll_{poll_no:04d}.json").write_text(json.dumps(data, indent=2))

        if first:
            print(f"[{ts()}] draftDetail keys: {sorted(detail.keys())}")
            if picks:
                print(f"[{ts()}] sample raw pick:\n{json.dumps(picks[0], indent=2)[:700]}")
            else:
                print(f"[{ts()}] no picks yet (draft not started / empty)")
            first = False

        if status != last_status:
            print(
                f"[{ts()}] status: inProgress={status[0]} drafted={status[1]} picks={status[2]}"
            )
            last_status = status

        new = [p for p in picks if p.get("overallPickNumber") not in seen]
        new.sort(key=lambda p: p.get("overallPickNumber") or 0)
        for p in new:
            seen.add(p.get("overallPickNumber"))
            print(f"[{ts()}] {describe_pick(p)}")

        if args.once:
            break
        time.sleep(args.interval)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print(f"\n[{ts()}] stopped.")
