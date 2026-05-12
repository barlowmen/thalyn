# ADR-0028 — Brain owns SQLite storage; Rust core accesses via IPC

- **Status:** Accepted
- **Date:** 2026-04-28
- **Supersedes:** —

## Context

`02-architecture.md` §3 and §5 carry an internal contradiction about which
process owns persistent storage:

- §3 states "the Rust core owns app state, the brain owns checkpoint and
  memory DBs."
- §5 attributes `app.db` (projects, providers, auth_backends, schedules,
  approvals, agent_runs, action_log, agent_records, routing_overrides) to
  the Rust core: "The Rust core writes this."

Today's reality matches neither cleanly. The Rust core has **no SQLite
dependency at all** — `src-tauri/Cargo.toml` lists `keyring` (OS keychain
for secrets) but no `sqlx`, `rusqlite`, or `tauri-plugin-sql`. The Python
brain owns every SQLite file: `app.db` with five tables (`agent_runs`,
`schedules`, `memory_entries`, `mcp_connectors`, `email_accounts`) — all
created via `CREATE TABLE IF NOT EXISTS` inline in
`brain/thalyn_brain/{runs,schedules,memory,mcp/registry,email/store}.py`
— plus per-run `runs/{run_id}.db` LangGraph checkpoints. The doc claim
"Rust core writes app.db" is aspirational language that never matched the
shipping code.

The v2 build introduces a set of new tables over several stages —
`THREAD`, `THREAD_TURN`, `SESSION_DIGEST`, `AGENT_RECORD`,
`AUTH_BACKEND`, `ROUTING_OVERRIDE`, plus a first-class `PROJECT` table,
`APPROVAL`, and `action_log`. Every later stage puts the *logic* that
reads and writes those tables in the brain: the eternal-thread write
path, the lead-graph delegation, the worker routing function, the
project classifier, the merge transaction, the drift critic. No stage
requires synchronous Rust-side SQL access that brain-via-IPC can't
serve.

There is also no migration runner today. `runs.py` has runtime
`ALTER TABLE ADD COLUMN` for forward-compat — fine for v1's tiny surface,
unsuitable as the v2 schema lands.

## Decision

**The brain sidecar (Python) owns all SQLite storage. The Rust core
accesses storage exclusively via IPC.** The OS keychain (via `keyring`)
remains the only Rust-owned persistence.

Concrete shape:

- **Stores.** All SQLite files live under the brain's data directory:
  `app.db` (projects, providers, auth_backends, schedules, approvals,
  agent_runs header, action_log header, agent_records, routing_overrides,
  mcp_connectors, email_accounts), `memory.db` (Mem0 + memory_entries
  with the five-tier scope), `thread.db` as a logical sub-store within
  `memory.db` (threads, thread_turns, session_digests), and per-run
  `runs/{run_id}.db` (LangGraph `AsyncSqliteSaver` checkpoints).
- **Migration runner.** Adopt **yoyo-migrations** as the brain's
  migration tool. SQL-file based, lockfile-coordinated, idempotent;
  matches the deterministic-runner constraint and avoids ORM lock-in.
  Migrations live at `brain/thalyn_brain/migrations/NNN_<slug>.sql`
  with `up` / `down` step markers per file; numbering picks the apply
  order.
- **First migration captures the v1 baseline schema** as
  `001_v1_baseline.sql`. The five existing stores' inline
  `CREATE TABLE IF NOT EXISTS` blocks are removed; schema lives only in
  the migration. The runner runs at brain startup before any store
  opens a connection.
- **Data directory.** The brain's `default_data_dir()` (or override via
  `THALYN_DATA_DIR`) is the single canonical path. The Rust core's
  brain supervisor sets `THALYN_DATA_DIR` to its resolved
  `app_data_dir()` when spawning the brain, so the two processes never
  diverge on where state lives.
- **Rust access pattern.** Rust calls into the brain over the existing
  NDJSON / JSON-RPC IPC for any data read or write. New v2 IPC methods
  follow the v1 pattern: a Tauri command that proxies to a brain
  JSON-RPC method.

## Consequences

- **Positive.**
  - **Matches reality.** No infrastructure churn; the brain-owned
    pattern that has been operational through v1 is ratified.
  - **Co-located logic and storage.** Every v2 stage has its logic in
    the brain; reads and writes are local, not IPC round-trips.
  - **Cross-table atomicity is local.** The merge transaction in the
    project-mobility stage needs to update `PROJECT`, `AGENT_RECORD`,
    `MEMORY_ENTRY`, and `ROUTING_OVERRIDE` together — all in one
    process, one DB, one transaction.
  - **Rust core stays minimal.** No SQL crate, no migration crate, no
    DB connection pool. The Rust core is the IPC broker, supervisor,
    CEF lifecycle owner, secrets adapter, and audit-log NDJSON writer
    — and that is the whole list.
  - **Migration runner solves a real problem now.** `runs.py`'s
    runtime `ALTER TABLE` retires; future schema changes go through
    yoyo with proper up/down scripts.
- **Negative.**
  - **Schedule wake-ups depend on the brain being up.** Already true
    today; the OS-level scheduler (launchd / Task Scheduler / systemd
    timers) starts Thalyn, which starts the brain, which reads
    schedule rows. Not a new constraint.
  - **A future "Rust-only mode"** (no brain) would need to reopen this
    decision. Not on the v1 roadmap and not currently a credible
    requirement.
- **Neutral.**
  - **Cross-store transactions across multiple SQLite files** (e.g.,
    a hypothetical merge that spans `app.db` and `memory.db`) are not
    atomic with the per-store-per-connection pattern the v1 stores
    established. The project-mobility stage is the first that may
    need this; the merge code will use a coordinated transaction
    across one shared connection or `ATTACH DATABASE` semantics.
    Flagged here so the merge stage doesn't get blindsided.
  - **The Rust↔brain data-dir mismatch** (Tauri-bundle-id'd
    `app_data_dir` vs. literal `Library/Application Support/Thalyn/data`)
    is a latent bug today; the `THALYN_DATA_DIR` forwarding above
    closes it.

## Alternatives considered

- **Rust core takes ownership of `app.db` per `02-architecture.md` §5's
  literal claim.** Rejected. No stage in the v2 build from v0.20
  through v0.37 functionally requires Rust-side SQL access.
  Adopting this would force a large infrastructure expansion — adding
  `sqlx` or `rusqlite`, a migration runner crate, and porting the
  read/write paths for v2 tables to Rust — for a benefit no planned
  stage realizes. Every subsequent stage's logic (Thalyn graph, lead
  graphs, classifier, merge transaction, drift critic) lives in the
  brain; co-locating storage in Rust would mean Rust↔brain round-trips
  on every DB op for the rest of the v2 build.
- **Split ownership: Rust owns the new tables introduced in the
  data-model stage (`THREAD`, `AGENT_RECORD`, `AUTH_BACKEND`, etc.);
  brain keeps the v1 tables.** Rejected. Creates a cross-language seam
  through the middle of related entities (an agent's `AGENT_RECORD`
  row in Rust, its `AGENT_RUN` rows in brain). Cross-table transactions
  become cross-language IPC dances. Strictly worse than either
  single-language choice.
- **Heavyweight migration tooling (alembic).** Rejected. Alembic
  couples to SQLAlchemy; the brain has no ORM and no plans to adopt
  one. yoyo's SQL-file format is closer to the
  `migrations/NNN_*.sql` convention `02-architecture.md` describes.
- **Hand-rolled migration runner with a `schema_versions` table.**
  Rejected as reinvention. yoyo provides exactly this with a tested
  lockfile and rollback semantics.

## Notes

`02-architecture.md` §3, §4.2, §5, and §14 (ADR index) update in a
companion commit that aligns the architecture-doc text with this ADR.

The going-public checklist gains no new rows from this decision —
brain-owned SQLite was already the implicit shape of every storage row
on the existing checklist.
