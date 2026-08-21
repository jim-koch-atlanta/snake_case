#!/usr/bin/env python3
"""One-off: split the 2016-2022 draft spreadsheet into per-year CSVs.

The older seasons arrived as a single .xlsx with one sheet per year, in a
different shape from the 2023-25 exports:

    Round 1                          <- separator row
    NO. | Player            | Team   <- header row
    1.0 | Derrick Henry Ten, RB | Boo Coodles

  - `NO.` is the pick within the round, written as a float ("1.0")
  - `Player` packs name, NFL team and position: "<name> <TEAM>, <POS>"
  - `Team` is the FANTASY manager, not the NFL team
  - the round comes from the separator rows, not a column

Output matches the existing draft-{year}.csv columns exactly, so
sources/historical.py reads old and new seasons the same way:

    NO.,PLAYER,,Position,Round,Keeper

`PLAYER` re-joins name and NFL team with a NON-BREAKING SPACE and the third
column (empty header) carries the clean name, both matching the 2023-25 files.

xlsx is a zip of XML, so this reads it with the standard library rather than
adding openpyxl for a one-off conversion.

    python3 tools/convert_historical_xlsx.py                 # writes the CSVs
    python3 tools/convert_historical_xlsx.py --dry-run       # report only
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HISTORICAL = ROOT / "data" / "historical"
XLSX = HISTORICAL / "Untitled spreadsheet.xlsx"

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

ROUND_RE = re.compile(r"^Round\s+(\d+)", re.I)
PICK_NO_RE = re.compile(r"^(\d+)(?:\.0+)?$")
#: "Derrick Henry Ten, RB" -> name / nfl team / position
PLAYER_RE = re.compile(r"^(.*?)\s+([A-Za-z/]+)\s*,\s*([A-Za-z/]+)\s*$")


def read_sheets(path: Path) -> dict[str, list[dict[str, str]]]:
    """{sheet name: [{column letter: value}]}, using only the stdlib."""
    z = zipfile.ZipFile(path)
    shared = [
        "".join(t.text or "" for t in si.iter(f"{{{NS}}}t"))
        for si in ET.fromstring(z.read("xl/sharedStrings.xml"))
    ]
    targets = {
        rel.get("Id"): rel.get("Target")
        for rel in ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    }
    out: dict[str, list[dict[str, str]]] = {}
    for sheet in ET.fromstring(z.read("xl/workbook.xml")).iter(f"{{{NS}}}sheet"):
        target = targets[sheet.get(f"{{{REL_NS}}}id")].lstrip("/")
        member = target if target.startswith("xl/") else f"xl/{target}"
        rows: list[dict[str, str]] = []
        for row in ET.fromstring(z.read(member)).iter(f"{{{NS}}}row"):
            cells: dict[str, str] = {}
            for c in row.iter(f"{{{NS}}}c"):
                column = re.match(r"([A-Z]+)", c.get("r")).group(1)
                v = c.find(f"{{{NS}}}v")
                if v is not None and v.text:
                    cells[column] = shared[int(v.text)] if c.get("t") == "s" else v.text
            rows.append(cells)
        out[sheet.get("name")] = rows
    return out


def convert_sheet(rows: list[dict[str, str]]) -> tuple[list[dict], list[str]]:
    """(csv rows, problems) for one year."""
    picks: list[dict] = []
    problems: list[str] = []
    current_round: int | None = None

    for raw in rows:
        a = (raw.get("A") or "").strip()
        if m := ROUND_RE.match(a):
            current_round = int(m.group(1))
            continue
        m = PICK_NO_RE.match(a)
        if not m:
            continue  # header row or blank
        if current_round is None:
            problems.append(f"pick {a} before any Round header")
            continue

        player_cell = (raw.get("B") or "").strip()
        pm = PLAYER_RE.match(player_cell)
        if not pm:
            problems.append(f"R{current_round} pick {a}: unparseable player {player_cell!r}")
            continue
        name, nfl_team, position = pm.group(1).strip(), pm.group(2).strip(), pm.group(3).strip()

        picks.append({
            "NO.": m.group(1),
            # matches the 2023-25 files: name and team joined by a NBSP
            "PLAYER": f"{name}\xa0{nfl_team}",
            "": name,
            "Position": position,
            "Round": str(current_round),
            # No keeper information exists in this spreadsheet. Left blank
            # rather than guessed -- see docs/session-log.md.
            "Keeper": "",
        })
    return picks, problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--xlsx", type=Path, default=XLSX)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not args.xlsx.exists():
        print(f"missing {args.xlsx}", file=sys.stderr)
        return 1

    fields = ["NO.", "PLAYER", "", "Position", "Round", "Keeper"]
    total_problems = 0
    for year, rows in sorted(read_sheets(args.xlsx).items()):
        picks, problems = convert_sheet(rows)
        rounds = sorted({int(p["Round"]) for p in picks})
        note = "" if len(rounds) == 22 else f"  <- {len(rounds)} rounds"
        print(f"  {year}: {len(picks)} picks, rounds {rounds[0]}-{rounds[-1]}{note}")
        for problem in problems:
            print(f"      ! {problem}")
        total_problems += len(problems)

        if not args.dry_run:
            out = args.xlsx.parent / f"draft-{year}.csv"
            with out.open("w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=fields)
                w.writeheader()
                w.writerows(picks)

    if args.dry_run:
        print("\n--dry-run: nothing written.")
    if total_problems:
        print(f"\n{total_problems} row(s) could not be parsed — see above.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
