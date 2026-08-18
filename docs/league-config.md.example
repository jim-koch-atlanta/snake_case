# League Configuration (canonical)

Everything the engine knows about the league lives here. If it's not here,
the code must not assume it.

## Basics

- Platform: ESPN
- League ID: `TODO`
- Season: 2026
- Teams: 12
- Draft: snake, order below, [DATE] [TIME]
- Pick clock: `TODO` seconds (verify in ESPN league settings — drives how
  much compute budget the between-picks loop has)

## Roster

| Slot | Count |
|---|---|
| QB | 1 |
| RB | 1 |
| RB/WR | 1 |
| WR/TE | 3 |
| DL | 2 |
| LB | 2 |
| DB | 2 |
| K | 1 |
| BE | 9 |
| IR | 3 |

22 spots; 3 keepers → **19 live picks per team, 228 live picks total**.

## Draft order

1. `TODO team name` (ESPN team_id: )
2. `TODO`
3. `TODO`
4. `TODO`
5. `TODO`
6. `TODO`
7. `TODO`
8. `TODO`
9. `TODO`
10. `TODO`
11. `TODO`
12. `TODO`

My team: `TODO` (position #`TODO`)

## Keepers (36 rows — this defines the real pick schedule)

| Team | Player | Kept in round |
|---|---|---|
| TODO | TODO | TODO |

## Scoring — offense

Fill from ESPN league settings → Scoring. Include EVERY non-zero rule.

| Stat | Points |
|---|---|
| Passing yards | TODO (per yd) |
| Passing TD | TODO |
| INT thrown | TODO |
| Rushing yards | TODO |
| Rushing TD | TODO |
| Reception | 0.5 |
| Receiving yards | TODO |
| Receiving TD | TODO |
| Fumble lost | TODO |
| 2-pt conversion | TODO |
| ... | |

## Scoring — IDP  ⚠️ highest-impact section in this file

Tackle-heavy vs big-play weighting completely changes which IDP positions
have VOR spread. Get these exact.

| Stat | Points |
|---|---|
| Solo tackle | TODO |
| Assisted tackle | TODO |
| Sack | TODO |
| Tackle for loss | TODO |
| QB hit | TODO |
| INT | TODO |
| Forced fumble | TODO |
| Fumble recovery | TODO |
| Pass defended | TODO |
| Defensive TD | TODO |
| Safety | TODO |

## Scoring — kicker

| Stat | Points |
|---|---|
| FG 0-39 | TODO |
| FG 40-49 | TODO |
| FG 50+ | TODO |
| XP | TODO |
| Missed FG | TODO |
