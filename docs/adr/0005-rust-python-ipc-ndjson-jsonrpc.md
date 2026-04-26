# ADR-0005 — Rust ↔ Python IPC: NDJSON + JSON-RPC 2.0 over `interprocess`

- **Status:** Accepted (provisional)
- **Date:** 2026-04-25

## Context

The Rust core and the brain sidecar (ADR-0004) need to exchange streaming agent output, tool calls, plan updates, approval prompts, and synchronous queries at sub-100 ms latency, on macOS, Linux, and Windows.

## Decision

Frame IPC as **NDJSON-delimited JSON-RPC 2.0 messages** over a Unix domain socket (macOS / Linux) or Windows named pipe (Windows). Use the Rust `interprocess` crate to abstract the socket type. Use `task-supervisor` (or equivalent) to supervise the sidecar process. The protocol surface is documented in `02-architecture.md` §6.

## Consequences

- **Positive.** Same wire format as MCP and LSP — every existing tool (debug visualizers, mock servers, log scrapers) works out of the box. Trivially diffable in a terminal (`tail -f socket.log`). Sub-10 ms latency over Unix socket; acceptable on Windows named pipe with batch coalescing if needed. Cancellation, back-pressure, and request correlation are all built into JSON-RPC's design.
- **Negative.** JSON serialization is heavier than binary — for very-high-throughput token streams we may add a `messagepack` mode later. Not a v1 concern.
- **Neutral.** Both sides need a JSON-RPC library; both ecosystems have mature ones (`jsonrpc-core` in Rust, `jsonrpcserver` / asyncio-native in Python).

## Alternatives considered

- **gRPC + tonic.** Strongly typed, fast; rejected as over-engineered for same-machine IPC and adds an HTTP/2 dependency.
- **MessagePack-RPC.** Slightly faster wire; rejected for less tooling familiarity. Reconsider if profiling shows JSON parsing is a hotspot.
- **WebSocket over loopback.** Rejected: adds a TCP + HTTP upgrade for no real gain over a Unix socket.
- **Plain stdin/stdout pipes.** Rejected for streaming: back-pressure semantics are awkward and we want bidirectional framing without inventing it.

## Notes

The brain sidecar's IPC surface is a versioned API — bumped on breaking changes — so brain and core can be tested independently. The exact methods and notifications live in `02-architecture.md` §6 and will be lifted into a JSON Schema in `docs/ipc-schema.json` during the relevant v0.x phase.
