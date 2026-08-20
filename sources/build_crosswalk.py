"""Build the player ID crosswalk. Spine = the ESPN player id.

Draft-day picks arrive from `mDraftDetail` as ESPN player ids, so every other
source (4for4 offense, 4for4 IDP, FantasyPros IDP, our own draft history) must
resolve to an ESPN id or it is useless live.

Emits two files:

  data/crosswalk.csv         exact + high-confidence automatic matches ONLY
  data/crosswalk_review.csv  everything else, one row per unresolved player,
                             with the top 3 candidates and a blank
                             `resolved_espn_id` column for hand review

Deliberately biased toward the review file. A wrong silent match puts the wrong
player on the board during a live draft; a long review queue merely costs an
evening. Anything ambiguous — a name that matches two ESPN players, or matches
one whose position disagrees — goes to review rather than being guessed.

    uv run python -m sources.build_crosswalk
    uv run python -m sources.build_crosswalk --refresh   # re-fetch ESPN players

Rebuilding REFUSES to clobber hand-review work: if the existing review file has
any `resolved_espn_id` filled in, or the crosswalk contains `hand_reviewed`
rows, the run aborts unless `--refresh` is passed. With `--refresh`, existing
resolutions are carried forward onto rows whose (source, source_key) still
matches, so a rebuild costs you nothing you had already decided.

NOT frozen by this script. Freezing happens after hand review (invariant #3).
"""

from __future__ import annotations

import argparse
import csv
import difflib
import json
import re
import sys
import unicodedata
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from engine.positions import UnknownPositionError, slot_for_position

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
ENV_PATH = ROOT / ".env"

ESPN_CACHE = DATA / "espn" / "players.json"
PROTEAM_CACHE = DATA / "espn" / "proteams.json"
OUT_MATCHED = DATA / "crosswalk.csv"
OUT_REVIEW = DATA / "crosswalk_review.csv"

# Decoded empirically from kona_player_info on 2026-08-19 (see session log).
ESPN_POSITION_ID = {
    1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K",
    9: "DT", 10: "DE", 11: "LB", 12: "CB", 13: "S",
}

# Auto-match only at or above this fuzzy score AND only when unambiguous.
# Everything below goes to review.
AUTO_FUZZY_FLOOR = 0.94
# Candidates scoring below this are not worth showing a human.
CANDIDATE_FLOOR = 0.70

SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}

# Sources disagree on team abbreviations (ESPN WSH/JAX vs FantasyPros WAS/JAC).
# Used ONLY to break ties between equally-good name matches — never as a match
# key, because players change teams between a projection export and draft day.
TEAM_ALIASES = {
    "WAS": "WSH", "JAC": "JAX", "LA": "LAR", "SD": "LAC", "OAK": "LV",
    "ARZ": "ARI", "TAM": "TB", "KAN": "KC", "NOR": "NO", "SFO": "SF",
    "GNB": "GB", "NWE": "NE", "NNY": "NYG", "HST": "HOU", "BLT": "BAL",
    "CLV": "CLE", "SL": "LAR", "STL": "LAR",
}


def normalize_team(raw: str) -> str:
    """Canonical team abbreviation for tie-breaking only."""
    t = str(raw).strip().upper()
    return TEAM_ALIASES.get(t, t)


# --------------------------------------------------------------------------
# name normalization
# --------------------------------------------------------------------------

def normalize_name(raw: str) -> str:
    """Collapse a player name to a comparable key.

    Handles the cases that actually occur in our data:
      - the non-breaking space (\\xa0) joining name+team in data/historical/
      - apostrophes and internal capitals: Tre'von Moehrig, Henry To'oTo'o
      - hyphens: Kool-Aid McKinstry, Akeem Davis-Gaither
      - suffixes: Jr. Sr. II III IV V
      - periods and accents: A.J. Brown, San Nicolás
    """
    s = unicodedata.normalize("NFKD", str(raw))
    s = "".join(c for c in s if not unicodedata.combining(c))
    # NBSP and friends -> plain space, before any splitting
    s = s.replace("\xa0", " ").replace("’", "'").replace("‘", "'")
    s = s.lower()
    # drop punctuation that sources disagree about; hyphen becomes a space so
    # "davis-gaither" and "davis gaither" collapse to the same key
    s = s.replace("-", " ").replace(".", "").replace("'", "").replace(",", "")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    parts = [p for p in s.split() if p]
    while len(parts) > 1 and parts[-1] in SUFFIXES:
        parts.pop()
    return " ".join(parts)


def similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


# --------------------------------------------------------------------------
# records
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class EspnPlayer:
    espn_id: int
    name: str
    team: str
    position: str
    slot: str
    norm: str


@dataclass(frozen=True)
class SourceRow:
    source: str
    key: str
    name: str
    team: str
    position: str
    slot: str
    norm: str


@dataclass
class Summary:
    matched: int = 0
    review: int = 0
    unmatched: int = 0
    reasons: dict = field(default_factory=dict)


# --------------------------------------------------------------------------
# ESPN spine
# --------------------------------------------------------------------------

def _env() -> dict[str, str]:
    env: dict[str, str] = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def fetch_espn_players(limit: int = 5000) -> dict:
    """Fetch the ESPN player universe.

    `sortPercOwned` is REQUIRED — without a sort key the endpoint 400s
    regardless of limit (verified 2026-08-19). The full universe is ~2472
    players; any limit above that returns all of them, so the default is set
    well clear of the ceiling.
    """
    env = _env()
    missing = [k for k in ("SWID", "ESPN_S2", "LEAGUE_ID") if not env.get(k)]
    if missing:
        raise RuntimeError(f"missing env vars {missing} in {ENV_PATH}")
    season = env.get("SEASON", "2026")
    url = (
        "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/"
        f"{season}/segments/0/leagues/{env['LEAGUE_ID']}?view=kona_player_info"
    )
    filt = {"players": {"limit": limit,
                        "sortPercOwned": {"sortAsc": False, "sortPriority": 1}}}
    req = urllib.request.Request(url, headers={
        "Cookie": f"SWID={env['SWID']}; espn_s2={env['ESPN_S2']}",
        "User-Agent": "Mozilla/5.0 (draft-copilot crosswalk)",
        "x-fantasy-filter": json.dumps(filt),
    })
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


def fetch_pro_teams() -> dict[str, str]:
    """proTeamId -> abbreviation, straight from ESPN rather than hardcoded."""
    env = _env()
    season = env.get("SEASON", "2026")
    url = (
        "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/"
        f"{season}?view=proTeamSchedules_wl"
    )
    req = urllib.request.Request(url, headers={
        "Cookie": f"SWID={env.get('SWID','')}; espn_s2={env.get('ESPN_S2','')}",
        "User-Agent": "Mozilla/5.0 (draft-copilot crosswalk)",
    })
    with urllib.request.urlopen(req, timeout=60) as r:
        d = json.loads(r.read())
    return {
        str(t["id"]): str(t.get("abbrev", "")).upper()
        for t in d.get("settings", {}).get("proTeams", [])
    }


def load_pro_teams(refresh: bool = False) -> dict[str, str]:
    if refresh or not PROTEAM_CACHE.exists():
        PROTEAM_CACHE.parent.mkdir(parents=True, exist_ok=True)
        PROTEAM_CACHE.write_text(json.dumps(fetch_pro_teams()))
    return json.loads(PROTEAM_CACHE.read_text())


def load_espn(refresh: bool = False) -> list[EspnPlayer]:
    if refresh or not ESPN_CACHE.exists():
        ESPN_CACHE.parent.mkdir(parents=True, exist_ok=True)
        ESPN_CACHE.write_text(json.dumps(fetch_espn_players()))
    pro_teams = load_pro_teams(refresh=refresh)
    raw = json.loads(ESPN_CACHE.read_text())
    out: list[EspnPlayer] = []
    skipped: dict[str, int] = {}
    for entry in raw.get("players", []):
        p = entry.get("player", {})
        pos = ESPN_POSITION_ID.get(p.get("defaultPositionId"))
        if pos is None:
            skipped[str(p.get("defaultPositionId"))] = skipped.get(str(p.get("defaultPositionId")), 0) + 1
            continue
        try:
            slot = slot_for_position(pos)
        except UnknownPositionError:
            skipped[pos] = skipped.get(pos, 0) + 1
            continue
        name = p.get("fullName") or ""
        out.append(EspnPlayer(
            espn_id=int(p["id"]), name=name,
            team=pro_teams.get(str(p.get("proTeamId", "")), ""),
            position=pos, slot=slot, norm=normalize_name(name),
        ))
    if skipped:
        print(f"  note: skipped ESPN players by position id/name: {skipped}", file=sys.stderr)
    return out


# --------------------------------------------------------------------------
# source loaders
# --------------------------------------------------------------------------

def load_4for4_offense() -> list[SourceRow]:
    path = DATA / "4for4" / "4for4_projections.csv"
    rows: list[SourceRow] = []
    with path.open(newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            name = (r.get("Player") or "").strip()
            pos = (r.get("Pos") or "").strip()
            if not name or not pos:
                continue
            rows.append(SourceRow(
                source="4for4_offense", key=(r.get("PID") or name).strip(), name=name,
                team=(r.get("Team") or "").strip(), position=pos,
                slot=slot_for_position(pos), norm=normalize_name(name),
            ))
    return rows


def load_4for4_idp() -> list[SourceRow]:
    rows: list[SourceRow] = []
    for group in ("db", "dl", "lb"):
        path = DATA / "4for4" / f"4for4-fantasy-football-projections-{group}-2026-table.csv"
        with path.open(newline="", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                name = (r.get("Player") or "").strip()
                pos = (r.get("Position") or "").strip()
                if not name or not pos:
                    continue
                team = (r.get("Team") or "").strip()
                rows.append(SourceRow(
                    source="4for4_idp", key=f"{group}:{name}|{team}", name=name,
                    team=team, position=pos, slot=slot_for_position(pos),
                    norm=normalize_name(name),
                ))
    return rows


def load_fantasypros() -> list[SourceRow]:
    path = DATA / "fantasypros" / "idp_clean.csv"
    if not path.exists():
        print(f"  note: {path} missing — run tools/clean_fantasypros.py", file=sys.stderr)
        return []
    rows: list[SourceRow] = []
    with path.open(newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            name = (r.get("player") or "").strip()
            rows.append(SourceRow(
                source="fantasypros_idp", key=f"{r.get('rank')}:{name}", name=name,
                team=(r.get("team") or "").strip(), position=(r.get("pos") or "").strip(),
                slot=(r.get("slot") or "").strip(), norm=normalize_name(name),
            ))
    return rows


def load_historical() -> list[SourceRow]:
    """Unique players across 2023-2025 drafts.

    The `PLAYER` column joins name+team with a NON-BREAKING SPACE; the clean
    name lives in the (unnamed) third column. Prefer the clean column, fall
    back to splitting PLAYER on the NBSP.
    """
    seen: dict[str, SourceRow] = {}
    for year in (2023, 2024, 2025):
        path = DATA / "historical" / f"draft-{year}.csv"
        if not path.exists():
            continue
        with path.open(newline="", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                clean = (r.get("") or "").strip()
                if not clean:
                    clean = (r.get("PLAYER") or "").replace("\xa0", " ").rsplit(" ", 1)[0].strip()
                pos = (r.get("Position") or "").strip()
                if not clean or not pos:
                    continue
                norm = normalize_name(clean)
                if norm in seen:
                    continue
                seen[norm] = SourceRow(
                    source="historical", key=clean, name=clean, team="",
                    position=pos, slot=slot_for_position(pos), norm=norm,
                )
    return list(seen.values())


# --------------------------------------------------------------------------
# matching
# --------------------------------------------------------------------------

def build_index(espn: list[EspnPlayer]) -> dict[str, list[EspnPlayer]]:
    idx: dict[str, list[EspnPlayer]] = {}
    for p in espn:
        idx.setdefault(p.norm, []).append(p)
    return idx


def candidates_for(row: SourceRow, espn: list[EspnPlayer], norms: list[str],
                   idx: dict[str, list[EspnPlayer]]) -> list[tuple[EspnPlayer, float]]:
    """Top candidates for a source row, best first."""
    exact = idx.get(row.norm, [])
    if exact:
        return [(p, 1.0) for p in exact]
    close = difflib.get_close_matches(row.norm, norms, n=5, cutoff=CANDIDATE_FLOOR)
    out: list[tuple[EspnPlayer, float]] = []
    for n in close:
        for p in idx.get(n, []):
            out.append((p, similarity(row.norm, n)))
    out.sort(key=lambda t: (-t[1], t[0].espn_id))
    return out[:5]


def classify(row: SourceRow, cands: list[tuple[EspnPlayer, float]]) -> tuple[str, str, EspnPlayer | None, float]:
    """Return (verdict, reason, chosen, score). verdict in matched/review/unmatched.

    Auto-matching requires ALL of: a single candidate at/above the floor, and
    agreement on roster slot. Team is used only to break ties among equally
    good name matches — players change teams, so it can never be a match key.
    """
    if not cands:
        return "unmatched", "no candidate above floor", None, 0.0

    top_score = cands[0][1]
    top = [c for c in cands if c[1] == top_score]

    if len(top) > 1:
        # ambiguous name — try team as a tie-breaker, but only to pick which
        # candidate to show first; still send it to review.
        if row.team:
            want = normalize_team(row.team)
            same_team = [c for c in top if normalize_team(c[0].team) == want]
            if len(same_team) == 1:
                return ("review", "ambiguous, team-disambiguated candidate shown first",
                        same_team[0][0], top_score)
        kind = "share this name" if top_score == 1.0 else "score identically"
        return "review", f"ambiguous: {len(top)} ESPN players {kind}", None, top_score

    chosen, score = top[0]
    if score < AUTO_FUZZY_FLOOR:
        return "review", f"best score {score:.2f} below auto floor {AUTO_FUZZY_FLOOR}", chosen, score
    if chosen.slot != row.slot:
        return "review", f"slot mismatch: source {row.slot} vs ESPN {chosen.slot}", chosen, score
    if score < 1.0:
        return "matched", "fuzzy name, slot agrees", chosen, score
    return "matched", "exact name, slot agrees", chosen, score


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def _prior_resolutions() -> dict[tuple[str, str], str]:
    """Existing hand-review answers, keyed by (source, source_key).

    Read back so a rebuild can carry them forward instead of discarding them.
    """
    if not OUT_REVIEW.exists():
        return {}
    out: dict[tuple[str, str], str] = {}
    with OUT_REVIEW.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            value = (row.get("resolved_espn_id") or "").strip()
            if value:
                out[(row.get("source", ""), row.get("source_key", ""))] = value
    return out


def _has_hand_reviewed() -> bool:
    if not OUT_MATCHED.exists():
        return False
    with OUT_MATCHED.open(newline="", encoding="utf-8-sig") as f:
        return any(r.get("match_type") == "hand_reviewed" for r in csv.DictReader(f))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--refresh", action="store_true",
        help="re-fetch the ESPN player list AND allow overwriting reviewed files "
             "(existing resolutions are carried forward)",
    )
    args = ap.parse_args()

    prior = _prior_resolutions()
    if prior and not args.refresh:
        print(
            f"REFUSING to rebuild: {len(prior)} hand-reviewed resolution(s) exist in "
            f"{OUT_REVIEW.name}"
            + (f" and {OUT_MATCHED.name} contains hand_reviewed rows" if _has_hand_reviewed() else "")
            + ".\n  Rebuilding would overwrite them. Pass --refresh to rebuild anyway "
              "(resolutions for unchanged players are carried forward).",
            file=sys.stderr,
        )
        return 1

    try:
        espn = load_espn(refresh=args.refresh)
    except (urllib.error.URLError, OSError, ValueError, KeyError, RuntimeError) as e:
        # network/auth/parse failures must be legible, not a raw traceback
        print(f"ERROR loading ESPN players: {type(e).__name__}: {e}", file=sys.stderr)
        print("  (cookies expire — refresh SWID/espn_s2 in .env)", file=sys.stderr)
        return 1
    if not espn:
        print("ERROR: no ESPN players loaded", file=sys.stderr)
        return 1

    idx = build_index(espn)
    norms = list(idx)
    dupe_names = {n: len(v) for n, v in idx.items() if len(v) > 1}
    print(f"ESPN spine: {len(espn)} players, {len(norms)} distinct normalized names, "
          f"{len(dupe_names)} names shared by >1 player")

    loaders = {
        "4for4_offense": load_4for4_offense,
        "4for4_idp": load_4for4_idp,
        "fantasypros_idp": load_fantasypros,
        "historical": load_historical,
    }

    matched_rows: list[dict] = []
    review_rows: list[dict] = []
    summaries: dict[str, Summary] = {}

    for source, loader in loaders.items():
        rows = loader()
        s = Summary()
        for row in rows:
            cands = candidates_for(row, espn, norms, idx)
            verdict, reason, chosen, score = classify(row, cands)
            s.reasons[reason] = s.reasons.get(reason, 0) + 1
            if verdict == "matched" and chosen is not None:
                s.matched += 1
                matched_rows.append({
                    "source": row.source, "source_key": row.key,
                    "source_name": row.name, "source_team": row.team,
                    "source_pos": row.position, "slot": row.slot,
                    "espn_id": chosen.espn_id, "espn_name": chosen.name,
                    "espn_pos": chosen.position, "match_type": reason,
                    "score": f"{score:.3f}",
                })
            else:
                if verdict == "unmatched":
                    s.unmatched += 1
                else:
                    s.review += 1
                out = {
                    "source": row.source, "source_key": row.key,
                    "source_name": row.name, "source_team": row.team,
                    "source_pos": row.position, "slot": row.slot,
                    "verdict": verdict, "reason": reason,
                    # carried forward from a previous review, if this player's
                    # (source, source_key) is unchanged
                    "resolved_espn_id": prior.get((row.source, row.key), ""),
                }
                for i in range(3):
                    if i < len(cands):
                        p, sc = cands[i]
                        out[f"cand{i+1}_espn_id"] = p.espn_id
                        out[f"cand{i+1}_name"] = p.name
                        out[f"cand{i+1}_pos"] = p.position
                        out[f"cand{i+1}_score"] = f"{sc:.3f}"
                    else:
                        out[f"cand{i+1}_espn_id"] = ""
                        out[f"cand{i+1}_name"] = ""
                        out[f"cand{i+1}_pos"] = ""
                        out[f"cand{i+1}_score"] = ""
                review_rows.append(out)
        summaries[source] = s

    OUT_MATCHED.parent.mkdir(parents=True, exist_ok=True)
    if matched_rows:
        with OUT_MATCHED.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(matched_rows[0]))
            w.writeheader()
            w.writerows(matched_rows)
    if review_rows:
        with OUT_REVIEW.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(review_rows[0]))
            w.writeheader()
            w.writerows(review_rows)

    print(f"\n{'source':<18} {'rows':>6} {'matched':>8} {'review':>7} {'unmatched':>10}")
    print("-" * 53)
    tot = Summary()
    for source, s in summaries.items():
        n = s.matched + s.review + s.unmatched
        print(f"{source:<18} {n:>6} {s.matched:>8} {s.review:>7} {s.unmatched:>10}")
        tot.matched += s.matched
        tot.review += s.review
        tot.unmatched += s.unmatched
    n = tot.matched + tot.review + tot.unmatched
    print("-" * 53)
    print(f"{'TOTAL':<18} {n:>6} {tot.matched:>8} {tot.review:>7} {tot.unmatched:>10}")

    print("\nreview/unmatched reasons:")
    agg: dict[str, int] = {}
    for s in summaries.values():
        for r, c in s.reasons.items():
            if "slot agrees" in r:
                continue
            agg[r] = agg.get(r, 0) + c
    for r, c in sorted(agg.items(), key=lambda x: -x[1]):
        print(f"  {c:>5}  {r}")

    print(f"\nwrote {OUT_MATCHED.relative_to(ROOT)} ({len(matched_rows)} rows)")
    print(f"wrote {OUT_REVIEW.relative_to(ROOT)} ({len(review_rows)} rows)")
    carried = sum(1 for r in review_rows if r.get("resolved_espn_id"))
    if carried:
        print(f"carried forward {carried} existing hand-review resolution(s)")
    print("\nNOT FROZEN. Fill `resolved_espn_id` in the review file, then run:")
    print("  uv run python -m sources.merge_crosswalk")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
