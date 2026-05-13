# ADR-0009 — Memory: Mem0 (semantic) + LangGraph checkpoints (run state) + project files

- **Status:** Accepted
- **Date:** 2026-04-29 (finalised at v0.25; provisional acceptance 2026-04-25)

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
`scope` enum (user / project / agent) and `kind` enum
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

### Finalisation at v0.25 — five tiers with explicit ownership

The original "three-tier" framing collapsed two separate concerns
into the cold tier (per-user vs per-project). v0.25 closes the
five-tier model called for in `01-requirements.md` §F6 and
`02-architecture.md` §5:

| Tier | Lifetime | Owner | Persisted? |
|---|---|---|---|
| **Working** | One LLM call (the prompt-builder's output) | Caller | No — ephemeral |
| **Session** | Today's working session, summarised at boundaries | Brain (rolling summarizer per ADR-0022) | `session_digests` table |
| **Project** | Project lifetime; archived with the project | Project lead | `memory_entries` (`scope='project'`) + `THALYN.md` |
| **Personal** | User lifetime, cross-project | Brain (writes); workers cannot write | `memory_entries` (`scope='personal'`) |
| **Episodic** | Forever, with explicit-prune affordance | Brain reads; the indexer writes on every turn | `thread_turns` + FTS5 (`thread_turn_index`) |
| **Agent** | Agent lifetime (lead / sub-lead / worker notes) | The agent itself | `memory_entries` (`scope='agent'`) |

The persisted `MEMORY_ENTRY.scope` enum is `project | personal |
episodic | agent`. The two ephemeral tiers (`working`, `session`)
appear in the user-facing vocabulary (`MEMORY_TIERS` in
`thalyn_brain/memory.py`) but the SQLite store rejects them on
insert — `working` is the prompt-builder's working set (no row),
and `session` lives in the `session_digests` table.

**Write paths are explicit, no silent profile-building (F6.6):**

- The brain writes any persisted scope. Personal-memory writes
  surface a confirmation in chat and emit a `memory_write`
  action-log entry.
- The lead writes `project` and `agent` scopes through the same
  helper. Workers reach project memory only through
  `record_worker_project_memory_write`, which fixes the scope at
  `project`, requires a `via_lead_id`, and tags the audit-log
  payload with `writerRole='worker'` so the renderer can drill
  into "Worker X wrote this through Lead Y".
- Workers cannot write `personal` memory at all; the API surface
  doesn't carry a path for it, and the validator rejects the
  scope from any worker-attributed call.

**Read paths follow ownership.** The eternal-thread context
assembler pulls personal memory into every turn whose distinctive
tokens didn't resolve in the recent window (the same heuristic
that gates eternal-transcript episodic recall). Project memory
loads at the lead-delegation hop: when the addressed lead's
project carries a `workspace_path`, the workspace's `THALYN.md`
folds in front of the lead's identity prompt. Agent memory is
read by the owning agent through its own namespace.

The migration that closes out the v1→v2 rename ships in
`007_memory_personal_scope.sql` (`scope='user'` → `scope='personal'`).

### Refinement at v0.37 — provenance fields on `MEMORY_ENTRY`

The five-tier model above settled *what* a memory row is and *who*
owns it. v0.37 adds *where it came from*, so the F1.10 drill-into-
source UX can navigate from a recalled memory row back to the
underlying claim — the same shape ADR-0027's `InfoFlowAuditReport`
uses for relayed claims. Without this, an episodic recall surface
in chat can render the row's body but can't link to "the lead's
report on 2026-04-30 at 11:14" or "the worker's tool call that
produced this fact."

`MEMORY_ENTRY.provenance` is a JSON-typed column carrying a shape
parallel to the audit `sourceRef`:

```jsonc
{
  // What kind of source produced this row. The renderer picks a
  // drawer (lead-chat, worker detail, editor, browser) based on
  // this kind.
  "kind": "thread_turn" | "worker_action" | "tool_call"
        | "file_diff" | "external_message" | "user",
  // Stable ids for the source. Naming mirrors the wire shape of
  // run.action_log / lead-chat refs so the renderer's existing
  // drill helpers compose.
  "turnId":   "...",     // thread_turn | external_message
  "runId":    "...",     // worker_action | tool_call
  "callId":   "...",     // tool_call
  "leadId":   "...",     // any lead-attributed write
  "filePath": "...",     // file_diff | editor-sourced
  "atMs":     1714512840000,
  // Optional free-text excerpt the recall surface can show
  // inline so the user sees the source phrasing before clicking
  // through.
  "excerpt": "Sam: 'three commits shipped overnight…'"
}
```

The column is **additive** and **optional** — v1 memory writes
that don't carry provenance (the legacy `record_*_memory_write`
helpers) keep working unchanged with `provenance` set to `NULL`.
New write paths that *do* know their source (the info-flow audit's
attached memory writes, the worker's tool-call → memory bridge,
the lead's report → personal-memory recap) populate the column at
write time. A future hardening pass can backfill historical rows
from the audit log when the user opts in.

**Schema landing.** The column ships in a migration paired with the
first write path that populates it. v0.37 settles the ADR; the
column lands when a memory-write codepath needs it (the
info-flow-audit → personal-memory recap is the natural first
caller). Until then, in-flight provenance flows through the
existing `confidence.audit.sourceRef` channel on `THREAD_TURN`
rows, which is enough for the chat surface to drill from a
relayed message.

**Read-side rendering.** The recall surface treats `provenance` as
metadata, not as part of the body: the body is what the LLM
prompted on, the provenance is what the user clicks to navigate
to. A `null` provenance renders the row without a drill-down link
(the row stands on its own, like any v1 memory) — no
gracefully-degrade contortions required.
