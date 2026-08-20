#!/usr/bin/env python3
"""One-off: turn the FantasyPros IDP cheat-sheet copy-paste into a real CSV.

`data/fantasypros/idp.csv` is not an export — it is a copy-paste of
https://www.fantasypros.com/nfl/rankings/idp-cheatsheets.php, so one player is
spread across four CRLF-terminated lines with tier markers interleaved:

    Tier 1\t \tCustomize Tiers
    9\t\t                       <- overall rank
    Myles Garrett (LAR)         <- name (team), sometimes trailing space
    DE1\t11\t-\t                <- position + positional rank, bye, SOS
    -                           <- ECR vs ADP

Reads the raw paste, writes a flat CSV, and leaves the original untouched
(source data is never destroyed). Fails loudly on any record it cannot parse
rather than silently dropping players — a missing IDP name is a hole in half
our starting lineup.

    python3 tools/clean_fantasypros.py

Positions are FantasyPros' NFL positions; `slot` maps them to our roster slots
(DL/LB/DB) via engine.positions, which is shared with the crosswalk builder and
the historical-draft reader.
"""

from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.positions import UnknownPositionError, slot_for_position

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "fantasypros" / "idp.csv"
OUT = ROOT / "data" / "fantasypros" / "idp_clean.csv"

RANK_RE = re.compile(r"^(\d+)\t*$")
NAME_RE = re.compile(r"^(.+?)\s*\(([A-Z]{2,3})\)\s*$")
POS_RE = re.compile(r"^([A-Z]+)(\d+)\t([^\t]*)\t([^\t]*)\t*$")
TIER_RE = re.compile(r"^Tier (\d+)\b")


def main() -> int:
    if not RAW.exists():
        print(f"missing {RAW}", file=sys.stderr)
        return 1
    raw = RAW.read_text(encoding="utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    lines = raw.split("\n")

    rows: list[dict] = []
    tier = None
    i = 0
    # skip the header line if present
    if lines and lines[0].startswith("RK\t"):
        i = 1

    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.strip() == "AD":
            # blank line, or an ad placeholder carried over from the web page
            i += 1
            continue
        if m := TIER_RE.match(line):
            tier = int(m.group(1))
            i += 1
            continue
        if m := RANK_RE.match(line):
            rank = int(m.group(1))
            try:
                name_line, pos_line, ecr_line = lines[i + 1], lines[i + 2], lines[i + 3]
            except IndexError:
                print(f"truncated record at rank {rank} (line {i+1})", file=sys.stderr)
                return 1

            nm = NAME_RE.match(name_line.strip())
            pm = POS_RE.match(pos_line)
            if not nm or not pm:
                print(
                    f"unparseable record at rank {rank} (line {i+1}):\n"
                    f"  name: {name_line!r}\n  pos : {pos_line!r}",
                    file=sys.stderr,
                )
                return 1

            pos = pm.group(1)
            try:
                slot = slot_for_position(pos)
            except UnknownPositionError as e:
                print(f"rank {rank}: {e}", file=sys.stderr)
                return 1

            rows.append(
                {
                    "rank": rank,
                    "player": nm.group(1).strip(),
                    "team": nm.group(2),
                    "pos": pos,
                    "pos_rank": int(pm.group(2)),
                    "slot": slot,
                    "bye": pm.group(3).strip(),
                    "sos": pm.group(4).strip(),
                    "ecr_vs_adp": ecr_line.strip(),
                    "tier": tier,
                }
            )
            i += 4
            continue
        print(f"unexpected line {i+1}: {line!r}", file=sys.stderr)
        return 1

    if not rows:
        print("no rows parsed", file=sys.stderr)
        return 1

    ranks = [r["rank"] for r in rows]
    if ranks != list(range(1, len(rows) + 1)):
        missing = sorted(set(range(1, max(ranks) + 1)) - set(ranks))
        print(f"WARNING: ranks not contiguous 1..{len(rows)}; missing {missing}", file=sys.stderr)

    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    by_slot: dict[str, int] = {}
    for r in rows:
        by_slot[r["slot"]] = by_slot.get(r["slot"], 0) + 1
    print(f"wrote {OUT.relative_to(ROOT)}: {len(rows)} players, tiers 1..{tier}")
    print(f"  by slot: {by_slot}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
