# ADR-0007 — Orchestration: LangGraph 1.0 + Claude Agent SDK at the nodes

- **Status:** Accepted (provisional)
- **Date:** 2026-04-25

## Context

We need a substrate that owns long-running agent workflows with checkpointing, human-in-the-loop interrupts, sub-agent composition, and resumability across app restarts. The Claude Agent SDK is excellent at running a single agent's loop (model + tools + MCP) but not at orchestrating workflows or surviving process death. LangGraph is the inverse — strong at orchestration, model-agnostic.

## Decision

Use **LangGraph 1.0** as the orchestration substrate; **Claude Agent SDK sessions** are invoked from within LangGraph nodes for the actual reasoning work. Each user-visible agent run is one LangGraph thread; sub-agents are LangGraph subgraphs. The standard graph shape is `plan → user_approves (interrupt) → execute → critic → respond`.

## Consequences

- **Positive.** Clean division of labor: LangGraph handles state, durability, interrupts, and graph topology; Claude Agent SDK handles tool calls, MCP, and provider details. Resumability across crashes "for free." Streaming, checkpointing, and approval gates are all idiomatic LangGraph features. Production-proven combination at scale.
- **Negative.** Two libraries to keep in version-pinned sync. LangGraph's API surface is large and we'll only use a subset; documentation is dense.
- **Neutral.** Both libraries are Python — keeps the brain sidecar single-runtime.

## Alternatives considered

- **Claude Agent SDK alone.** Loses durability, easy human-in-the-loop, and explicit graph topology — would need to be reinvented.
- **LangGraph alone (no Claude Agent SDK).** Loses Claude's first-class file/tool/MCP integration; we'd reimplement those at the node level.
- **AutoGen / CrewAI / OpenAI Agents SDK.** Considered; rejected for less-mature durability or less production traction in 2026.
- **Build our own orchestrator on top of just the SDK.** Considered; rejected — checkpointing + interrupts + graph topology is a multi-quarter effort we don't need to redo.

## Notes

Pin LangGraph and Claude Agent SDK exact versions; review at every architecture review. The brain↔orchestrator wiring is the topmost-risk integration point — covered as `02-architecture.md` §12 risk #4.

### Refinement at v0.6 implementation

Two implementation details deviated from the original sketch when sub-agents landed; the **Decision** above is unchanged.

- **Sub-agents are independent LangGraph runs, not LangGraph subgraphs.** The original sketch said *"sub-agents are LangGraph subgraphs."* The implementation instead spawns each sub-agent as a separate top-level LangGraph run with its own `thread_id`, its own per-run SqliteSaver checkpoint db (`runs/{run_id}.db`), its own audit log file, and a `parent_run_id` link in the runs index. LangGraph's idiomatic subgraph pattern is single-checkpointer + `checkpoint_ns` namespace differentiation; that pattern is the right call when sub-agents are pure decomposition, but Thalyn's sub-agents are first-class navigable runs (the user opens, kills, takes over each on its own terms), so independent persistence — one db per run, archivable per run — fits better. The execute node consults a runner-supplied `SubAgentSpawner` callback when a plan node carries `subagentKind`; the spawner increments depth, creates the child run, and propagates the same closure so deeper trees can grow up to a configurable depth cap (default 2). Spawns that exceed the cap surface a `run.approval_required` notification with `gateKind: "depth"` and are recorded as skipped in the audit log.
- **Take-over is renderer-side.** `02-architecture.md` §6.1 listed `run.takeover` as a brain JSON-RPC method. The v0.6 implementation didn't need one — the renderer materialises the take-over snapshot from `runs.get` (title, plan, final response) into a fresh chat session's system prompt, force-remounting the chat surface via React keying so session id, message list, and provider history all reset cleanly. A brain-side endpoint can land later if richer history (action-log replay, tool-output context) becomes useful, but it's optional, not load-bearing.
