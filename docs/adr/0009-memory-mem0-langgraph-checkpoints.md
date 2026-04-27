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

### Refinement at v0.11 implementation

The v0.11 phase shipped the memory access layer + JSON-RPC + UI
without committing to a recall engine. The shape behind the API
is **SQLite + plain-text LIKE search**: same schema as the cold-
tier above (memory_id, scope, kind, body, author, timestamps),
shared SQLite app.db file, no semantic embeddings yet. The
`scope` enum (user / project / agent / global) and `kind` enum
(fact / preference / reference / feedback) are already wired so
the recall engine is a drop-in upgrade behind the same surface.

The **Mem0 vs LangMem** decision the post-v0.6 review flagged
was settled at the post-v0.12 architecture review. Mem0 is the
recall engine when semantic recall lands on top of the v0.11
SQLite layer:

- **Latency disqualifies LangMem.** 2026 benchmarks report
  ~60 s p95 search latency on LangMem versus ~0.2 s on Mem0.
  LangMem's docs explicitly say "never use for synchronous
  retrieval." Our recall path is synchronous (prompt-build
  time), so LangMem is out.
- **Memory shape matches.** Mem0's three-tier scopes (user,
  session, agent) line up with the `scope` enum we already
  ship. LangMem's flat key-value store would force a schema
  collapse.
- **Integration breadth.** Mem0 has Python + JS SDKs plus a
  LangGraph integration. LangMem is Python-only and LangGraph-
  native; the ergonomics are tighter for our stack but the
  latency story is disqualifying.

`THALYN.md` (project file tier) was implemented in v0.11 too —
chat.send loads it from `workspaceRoot` and merges it into the
system prompt; `projectContext` echoes back in the response.
This validates the three-tier-not-one-store decision: the user
edits `THALYN.md` directly when they want code-versioned
context, and the in-flight tier (session) and cold tier (Mem0
when we light it up) carry the rest.
