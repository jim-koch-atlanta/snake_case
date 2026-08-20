"""Fold hand-reviewed resolutions from crosswalk_review.csv into crosswalk.csv.

`sources/build_crosswalk.py` deliberately refuses to guess, and parks everything
ambiguous in `data/crosswalk_review.csv` with a blank `resolved_espn_id`. This
is the other half: once that column is filled in by hand, merge those decisions
back into the crosswalk so they actually reach the draft board.

    uv run python -m sources.merge_crosswalk            # merge, report, write
    uv run python -m sources.merge_crosswalk --dry-run  # report only
    uv run python -m sources.merge_crosswalk --allow-partial   # ignore blanks

Rows merged this way are stamped `match_type: hand_reviewed` so they are
distinguishable from automatic matches forever after — and so a rebuild can
tell the difference between "the matcher decided this" and "a human did".

To record that a source player has NO ESPN counterpart (retired, practice
squad, a stale row in a projection export), put one of NO_MATCH_SENTINELS in
`resolved_espn_id` rather than leaving it blank. Blank means "not reviewed
yet"; a sentinel means "reviewed, and the answer is nobody".
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CROSSWALK = DATA / "crosswalk.csv"
REVIEW = DATA / "crosswalk_review.csv"
ESPN_CACHE = DATA / "espn" / "players.json"

#: Written into `resolved_espn_id` to mean "reviewed; no ESPN player exists".
NO_MATCH_SENTINELS = frozenset({"none", "no match", "nomatch", "n/a", "na", "-", "x"})

CROSSWALK_FIELDS = [
    "source", "source_key", "source_name", "source_team", "source_pos", "slot",
    "espn_id", "espn_name", "espn_pos", "match_type", "score",
]

HAND_REVIEWED = "hand_reviewed"

ESPN_POSITION_ID = {
    1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K",
    9: "DT", 10: "DE", 11: "LB", 12: "CB", 13: "S",
}


class MergeError(Exception):
    """Raised when the merge cannot proceed safely."""


def load_espn_spine() -> dict[str, dict]:
    """espn_id -> player record, for filling in names and sanity-checking.

    Absence from this cache is NOT an error: the spine is ESPN's top ~2472 by
    percent owned, and a reviewer looking up a deep-roster player by hand will
    legitimately produce ids outside it.
    """
    if not ESPN_CACHE.exists():
        return {}
    raw = json.loads(ESPN_CACHE.read_text())
    return {str(e["player"]["id"]): e["player"] for e in raw.get("players", [])}


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        raise MergeError(f"missing {path}")
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def classify_resolution(raw: str) -> tuple[str, str]:
    """(kind, value) where kind is 'blank' | 'no_match' | 'espn_id' | 'invalid'."""
    v = (raw or "").strip()
    if not v:
        return "blank", ""
    if v.lower() in NO_MATCH_SENTINELS:
        return "no_match", v
    if v.isdigit():
        return "espn_id", v
    return "invalid", v


def candidate_lookup(row: dict, espn_id: str) -> tuple[str, str]:
    """(name, pos) for `espn_id` from the row's own candidate columns, if present."""
    for i in (1, 2, 3):
        if (row.get(f"cand{i}_espn_id") or "").strip() == espn_id:
            return (row.get(f"cand{i}_name") or "").strip(), (row.get(f"cand{i}_pos") or "").strip()
    return "", ""


def merge(
    crosswalk: list[dict],
    review: list[dict],
    spine: dict[str, dict],
    allow_partial: bool = False,
) -> tuple[list[dict], dict]:
    """Return (merged_rows, report). Pure apart from the caller's file I/O."""
    report: dict = {
        "auto": len(crosswalk),
        "merged": 0,
        "no_match": 0,
        "blank": 0,
        "invalid": [],
        "off_spine": [],
        "slot_differs": [],
        "duplicate_ids": [],
        "replaced": 0,
    }

    # keyed by (source, source_key) so a reviewed row supersedes any auto row
    out: dict[tuple[str, str], dict] = {
        (r["source"], r["source_key"]): r for r in crosswalk
    }

    for row in review:
        kind, value = classify_resolution(row.get("resolved_espn_id", ""))
        key = (row["source"], row["source_key"])

        if kind == "blank":
            report["blank"] += 1
            continue
        if kind == "invalid":
            report["invalid"].append((row["source"], row["source_name"], value))
            continue
        if kind == "no_match":
            report["no_match"] += 1
            out.pop(key, None)  # explicitly not in the crosswalk
            continue

        player = spine.get(value)
        if player is not None:
            espn_name = player.get("fullName", "")
            espn_pos = ESPN_POSITION_ID.get(player.get("defaultPositionId"), "")
        else:
            espn_name, espn_pos = candidate_lookup(row, value)
            report["off_spine"].append((row["source_name"], value))

        if espn_pos and row.get("slot") and _slot_of(espn_pos) != row["slot"]:
            report["slot_differs"].append(
                (row["source_name"], row["slot"], _slot_of(espn_pos), espn_pos)
            )

        if key in out:
            report["replaced"] += 1
        out[key] = {
            "source": row["source"],
            "source_key": row["source_key"],
            "source_name": row["source_name"],
            "source_team": row.get("source_team", ""),
            "source_pos": row.get("source_pos", ""),
            "slot": row.get("slot", ""),
            "espn_id": value,
            "espn_name": espn_name,
            "espn_pos": espn_pos,
            "match_type": HAND_REVIEWED,
            "score": "",
        }
        report["merged"] += 1

    if report["blank"] and not allow_partial:
        raise MergeError(
            f"{report['blank']} review row(s) still have a blank resolved_espn_id. "
            f"Fill them in, use one of {sorted(NO_MATCH_SENTINELS)} for 'no ESPN "
            "player exists', or pass --allow-partial to merge what is done."
        )
    if report["invalid"]:
        raise MergeError(
            "resolved_espn_id must be a number or a no-match sentinel; got: "
            + ", ".join(f"{s}/{n}={v!r}" for s, n, v in report["invalid"][:8])
        )

    # a single ESPN id claimed by two different players within one source
    by_id: dict[tuple[str, str], set[str]] = {}
    for r in out.values():
        by_id.setdefault((r["source"], r["espn_id"]), set()).add(r["source_name"])
    report["duplicate_ids"] = [
        (src, eid, sorted(names))
        for (src, eid), names in sorted(by_id.items())
        if len(names) > 1
    ]

    rows = sorted(out.values(), key=lambda r: (r["source"], r["source_name"]))
    return rows, report


_SLOT_OF = {"QB": "QB", "RB": "RB", "WR": "WR", "TE": "TE", "K": "K",
            "DE": "DL", "DT": "DL", "LB": "LB", "CB": "DB", "S": "DB"}


def _slot_of(position: str) -> str:
    return _SLOT_OF.get(position.strip().upper(), position.strip().upper())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="report without writing")
    ap.add_argument("--allow-partial", action="store_true",
                    help="merge even if some rows are still unreviewed")
    args = ap.parse_args()

    try:
        crosswalk = read_csv(CROSSWALK)
        review = read_csv(REVIEW)
        rows, rep = merge(crosswalk, review, load_espn_spine(), args.allow_partial)
    except MergeError as e:
        print(f"MERGE ERROR: {e}", file=sys.stderr)
        return 1

    print(f"automatic matches in {CROSSWALK.name}: {rep['auto']}")
    print(f"hand-reviewed rows merged           : {rep['merged']}")
    if rep["replaced"]:
        print(f"  (of which superseded an auto row  : {rep['replaced']})")
    if rep["no_match"]:
        print(f"recorded as 'no ESPN player'        : {rep['no_match']}")
    if rep["blank"]:
        print(f"still unreviewed (left out)         : {rep['blank']}")
    print(f"total rows after merge              : {len(rows)}")

    if rep["off_spine"]:
        print(f"\n{len(rep['off_spine'])} id(s) not in the cached ESPN spine — expected for "
              "deep-roster players looked up by hand, not an error:")
        for name, eid in rep["off_spine"][:10]:
            print(f"   {name:<24} {eid}")
        if len(rep["off_spine"]) > 10:
            print(f"   ... and {len(rep['off_spine']) - 10} more")

    if rep["slot_differs"]:
        print(f"\n{len(rep['slot_differs'])} player(s) where the source's roster slot differs "
              "from ESPN's. ESPN governs lineup legality, so ESPN's slot is what "
              "the player can actually fill:")
        for name, src_slot, espn_slot, espn_pos in rep["slot_differs"][:20]:
            print(f"   {name:<24} source {src_slot:<3} -> ESPN {espn_slot} ({espn_pos})")

    if rep["duplicate_ids"]:
        print(f"\n{len(rep['duplicate_ids'])} ESPN id(s) claimed by more than one name within "
              "a source — usually a legitimate alias, worth an eyeball:")
        for src, eid, names in rep["duplicate_ids"]:
            print(f"   {src:<16} {eid:<10} {names}")

    if args.dry_run:
        print("\n--dry-run: nothing written.")
        return 0

    with CROSSWALK.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CROSSWALK_FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"\nwrote {CROSSWALK.relative_to(ROOT)} ({len(rows)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
