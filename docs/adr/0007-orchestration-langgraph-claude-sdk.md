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
