# 0022 — Eternal-thread durability: write-before-emit, FTS5 episodic index, rolling digests

- **Status:** Proposed
- **Date:** 2026-04-28
- **Deciders:** Barlow
- **Supersedes:** —
- **Superseded by:** —

## Context

The eternal thread is the v2 chat surface (`02-architecture.md` §9,
`01-requirements.md` §F1). Per NFR2 it carries a class-A correctness
invariant: a turn the user committed must never be silently lost.
Restarts, crashes, OS reboots, and `kill -9` mid-stream are all in scope
for *no message loss*. The phase v0.21 goal text reads "the brain can
answer 'what did we say about X last week?' correctly," which implies
durability *and* searchable recall against the full transcript.

Three sub-decisions are entangled and the Phase v0.21 build plan asks
for them together:

1. **Persistence ordering and fsync semantics.** When does the row hit
   stable storage relative to the user-visible streaming response?
2. **Episodic index choice.** ADR-0009 named Mem0 as the chosen recall
   engine when "semantic recall lands"; the v0.11 refinement on the
   same ADR shipped plain SQLite + LIKE for the *memory* layer and
   said the engine is a drop-in upgrade behind the surface. The
   eternal-thread surface needs more than LIKE (the test asserts a
   "30 days ago" query lands the right turn in the top 3) but does
   not yet need vector-grade recall.
3. **Rolling summarizer storage.** §9.3 calls for `SESSION_DIGEST`
   rows at session boundaries, with second-level summarization when
   the digest table exceeds a budget.

The constraint that pulls everything together is "the index is
rebuildable from `THREAD_TURN`." That makes the index a cache; the
write path's only durability obligation is the turn row itself.

## Decision

**1. Write-before-emit, with `fsync` on every committed turn.**
On `thread.send`, the brain inserts the user's `THREAD_TURN` row with
`status='in_progress'` *before* emitting `thread.chunk{kind:start}`.
The brain's reply turn writes on the *completed* boundary — after the
final chunk is buffered, the row is inserted with `status='completed'`
and the user turn is flipped to `'completed'` in the same transaction.
Both writes use `PRAGMA synchronous=FULL` and an explicit
`PRAGMA wal_checkpoint(FULL)` on commit so the row is durable against
power loss, not just process crash. (`fsync` is what
`synchronous=FULL` produces under SQLite's WAL.) On startup the brain
scans for `status='in_progress'` rows and surfaces a
`thread.recovery_required` notification carrying the half-completed
turn — the renderer renders it as a "your last message got cut off —
want me to retry?" affordance. The architecture's §9.5 mitigation
table lists this case explicitly.

**2. SQLite FTS5 as the episodic index, with Mem0 deferred.**
FTS5 is bundled with the Python `sqlite3` build we already ship, has
sub-millisecond query latency, and supports BM25 ranking which is
strong enough for the phase exit criteria (top-3 hit on a known-topic
query against ~100 historical turns). The FTS5 vtable is keyed by
`turn_id` and carries `body` and `role` content; the brain inserts /
deletes the FTS row inside the same transaction as the
`thread_turns` write. The pre-existing `episodic_index_ptr_json`
column on `thread_turns` carries the FTS rowid for explicit lookup.
A reconciler rebuilds the FTS index from `thread_turns` if it ever
drifts. ADR-0009 stays the source of truth for *memory* recall —
this ADR scopes only to the eternal-thread surface — and the v2
upgrade path is explicit: a future ADR can swap FTS5 for Mem0 (or a
hybrid) behind the same `thread.search` IPC.

**3. Rolling summarizer triggered by 20-min idle gap or explicit
`/wrap`, writing `SESSION_DIGEST` rows.** A LangGraph node inside the
Thalyn graph runs the summarizer; the summarizer's prompt produces a
structured-JSON payload (topics, decisions, open threads) the brain
re-loads as part of context assembly on the next turn. When the live
`session_digests` table grows past a configurable budget (default:
40 digests per thread), a second-level summarizer compresses the
oldest N into a single parent digest, with the parent's `digest_id`
recorded on the children via the existing
`second_level_summary_of` column. Triggering: a 20-min idle gap is
detected by comparing `now()` against the most recent
`thread_turns.at_ms`; explicit `/wrap` calls `digest.run` directly.

**4. Context assembly per turn (§9.4) is bounded.** Each `thread.send`
assembles: system prompt + the latest digest + the recent ~30–50
turns verbatim + (conditional) episodic recall when the user's input
references unresolved tokens (a heuristic: prompt contains a date /
proper noun / quoted phrase not present in the recent window). The
recent-window cap is the load-bearing knob; episodic recall is
opt-in per turn so a chatty bursty session doesn't pay the FTS cost
on every send.

## Consequences

- **Positive.** No new runtime dependency for v0.21 (FTS5 is bundled);
  the durability semantics are explicit and testable; the index is a
  rebuildable cache so a corrupt FTS row is not a class-A bug. The
  recovery prompt is user-facing rather than silent — F1 / F12.7's
  "no silent loss" reading. The summarizer's structured output is
  inspectable by the user (the going-public-checklist's
  "user can inspect the latest digest in settings" is unblocked by
  this shape).
- **Negative.** FTS5 is lexical, not semantic. A user search for
  "the auth refactor" will miss a turn that said "the login token
  rewrite." Mitigation: BM25 + body + role indexing carries us
  through v0.21–v0.24; the Mem0 swap is a single ADR + a behind-the-
  surface migration.  `synchronous=FULL` is slower than the existing
  `WAL` default; the cost is measured in microseconds per turn write
  on healthy SSDs, which is well inside the per-turn latency budget.
- **Neutral.** The `thread_turns.status` column is a new addition —
  migration 005 lands the column with a default of `'completed'`
  so historical rows look right after the upgrade. `thread.db` stays
  a logical sub-store inside `app.db` per ADR-0028; the physical
  split is a later-phase concern and does not block this ADR.

## Alternatives considered

- **Mem0 as the v0.21 episodic index.** Rejected for now — the
  dependency adds packaging surface (Mem0 pulls a vector store and
  several model SDKs) and the phase exit criteria are met by FTS5.
  ADR-0009's pointer to Mem0 stays the post-v0.21 plan.
- **LangMem / LangGraph-native memory.** Rejected on latency
  (~60s p95 search per ADR-0009's refinement). Eternal-thread recall
  is on the synchronous path of `thread.send`; that latency is
  disqualifying.
- **Write-after-emit (post-stream commit only).** Rejected — a
  power cut between "user saw the response" and "row hit disk" is
  the class-A failure mode the invariant is designed to prevent.
- **`status` as a separate `thread_turn_recovery` table.** Rejected
  on simplicity — a column is one less FK to keep consistent and
  the recovery scan is `WHERE status = 'in_progress'`, fast under
  the existing index.
- **A duration-based 30-min soak test in CI per push.** The phase
  prose asks for it. Concretely we ship a randomized-kill-point
  Python soak harness that defaults to a short (~60s) duration in
  the per-push CI gate and exposes a `THALYN_SOAK_DURATION_SECS`
  env var the going-public hardening pass dials to 1800. This
  trade-off is documented in `docs/going-public-checklist.md` so
  the public-release-grade gate is explicit.

## References

- `01-requirements.md` §F1 (eternal thread), §F6 (memory), §NFR2 (durability).
- `02-architecture.md` §5 (data model), §9 (eternal thread).
- ADR-0008 (LangGraph SqliteSaver — durable run state).
- ADR-0009 (Mem0 + LangGraph + project files — memory tiers).
- ADR-0028 (brain owns SQLite storage — physical-store boundaries).
- SQLite FTS5 docs — https://sqlite.org/fts5.html.
- BM25 ranking under FTS5 — https://sqlite.org/fts5.html#the_bm25_function.
