# ADR-0009 — Memory: Mem0 (semantic) + LangGraph checkpoints (run state) + project files

- **Status:** Accepted (provisional)
- **Date:** 2026-04-25

## Context

Long-running agents require memory at three time horizons: within a single agent step (LLM context window), within a run (state across nodes), and across runs / sessions (facts, preferences, project conventions). Single-store solutions either bloat the prompt (everything in context) or lose continuity (everything in retrieval).

## Decision

Three-tier memory:

1. **In-context (hot).** Recent N turns + structured plan/state, included in every LLM call.
2. **Run state (warm).** LangGraph SqliteSaver (ADR-0008). Per-run, survives restarts.
3. **Cross-session (cold).** **Mem0** as the semantic + entity-graph store, plus project-level `THALYN.md` files committed to the user's repo, plus a user-level memory store.

## Consequences

- **Positive.** Each tier solves one problem; no single store has to be everything. `THALYN.md` is human-editable and versioned with code, so the user has direct control. Mem0 is OSS, lightweight, and well-documented.
- **Negative.** Three stores means three places to think about memory bugs. Mem0's persistence layer (SQLite under the hood for our deployment) is fine but adds another dep.
- **Neutral.** Agent writes to memory always surface to the user (`01-requirements.md` F8.4) — no silent profile-building.

## Alternatives considered

- **Zep.** Excellent for time-indexed graphs but token-cost heavier; rejected as overkill for our scope.
- **Mem0 only.** Loses the human-editable project-file tier.
- **Project files only.** Loses semantic recall.

## Notes

Mem0 vs Zep is a candidate for the v0.6 architecture review if usage patterns suggest we need the time-indexed angle.
