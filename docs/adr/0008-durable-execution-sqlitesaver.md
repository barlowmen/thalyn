# ADR-0008 — Durable execution: LangGraph SqliteSaver (DBOS-style)

- **Status:** Accepted (provisional)
- **Date:** 2026-04-25

## Context

Agent runs can last 8 hours unattended (`01-requirements.md` F5). They must survive Thalyn restarts, OS reboots, and crashes. Durable-execution servers (Temporal, Restate) are designed for this but require a server we're not willing to ship. The user is local; the state is local.

## Decision

Use **LangGraph's `SqliteSaver` checkpointer** for run state, persisted to per-run SQLite files (`runs/{run_id}.db`). Resumability follows the DBOS pattern: every node transition writes its outputs before the next node runs; on restart, we read the last completed checkpoint and resume from the next node.

## Consequences

- **Positive.** Zero infrastructure — a SQLite file per run. Fast (sub-ms write latency on a healthy disk). Trivially backed up, archived, or deleted by the user. Standard LangGraph idiom; no custom durability code to write.
- **Negative.** Per-run files mean many SQLite files for a heavy user. Mitigation: the Rust core periodically rolls completed runs into an archive directory and (on user opt-in) compresses them.
- **Neutral.** No multi-machine resumption — but the project is single-user-on-laptop, so this is fine.

## Alternatives considered

- **PostgresSaver.** Requires a Postgres instance; overkill for desktop.
- **In-memory + periodic snapshot.** Loses ground on resumability; checkpointing every step is the safer default.
- **Temporal / Restate.** Rejected — these are server-based and we don't ship a server.

## Notes

A separate "runs index" lives in `app.db` so the UI can list all runs without opening every per-run DB.
