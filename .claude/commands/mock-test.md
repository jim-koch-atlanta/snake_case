---
description: Run the mock-draft validation loop after engine changes
---

Run the full mock-draft validation loop:

1. `uv run pytest` — if anything fails, stop and fix before proceeding.
2. Replay every recorded mock draft in `data/mocks/` through the engine with
   `uv run python -m tools.mock_replay`.
3. For each replay, report:
   - Any pick where the engine's #1 recommendation was unavailable (state bug).
   - Any point where the feasibility guard (mandatory slots vs picks
     remaining) went negative without a prior warning state.
   - The engine's recommended pick vs. my actual pick at each of my turns,
     with the VONA delta.
   - Total wall-clock time per recomputation — flag anything over 5 seconds.
4. Summarize: is the engine's advice diverging from ADP in ways that are
   explainable (IDP scarcity, keeper-adjusted survival) or in ways that look
   like bugs? Be specific about which.

Do not "fix" divergences by nudging the model toward ADP — divergence is the
point. Only flag divergence that lacks a coherent explanation.
