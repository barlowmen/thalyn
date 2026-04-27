# ADR-0017 — Observability: OpenTelemetry GenAI + self-hosted Langfuse

- **Status:** Accepted (provisional)
- **Date:** 2026-04-25

## Context

Long-running agent runs need observability — token spend, tool-call sequences, latency, drift indicators, and reproducibility for debugging. We do not ship telemetry to a Thalyn-operated server (`01-requirements.md` F10.3 / OQ-8). Observability must therefore be local-first and opt-in to anything cloud.

## Decision

- **OpenTelemetry GenAI semantic conventions** as the instrumentation layer in the brain sidecar. Every LLM call, tool invocation, and LangGraph node transition emits a span.
- **Self-hosted Langfuse** as the default observability backend, shipped as a `docker-compose.yml` in `observability/` that the user can start with one command. All data stays on the user's machine.
- If observability is disabled (default-off until the user enables it), spans go to a no-op exporter; no agent code path changes.
- Optional user-provided Sentry DSN for error reporting (`01-requirements.md` F10.3) — separate from the OTel pipeline; runtime exceptions only.

## Consequences

- **Positive.** Standard instrumentation; future-proof against backend changes. Self-hosted Langfuse means rich UI without sending data anywhere. Ship-cost for observability is one Docker Compose file the user opts into.
- **Negative.** Docker Compose dependency for the optional Langfuse instance — a heavier opt-in than "open a panel and look." Mitigation: Langfuse runs locally only when the user wants it; otherwise OTel spans are no-ops.
- **Neutral.** OTel GenAI semconv is still experimental in early 2026; we accept some churn in span shapes.

## Alternatives considered

- **Custom in-app observability UI.** Considered; rejected — Langfuse's UI is mature and we shouldn't build our own.
- **Send spans to a Thalyn-operated server.** Rejected; violates F10.3.
- **Arize Phoenix.** Considered; Langfuse is the slightly better fit for agent traces and OSS license.

## Notes

A v0.12 (observability) phase task is to validate the user-facing onboarding: how do we make starting Langfuse a one-click affair from the settings panel?

### Refinement at v0.14 implementation — instrumentation surface

The first observability commit ships the SDK init plus the high-value spans. Three details worth pinning:

- **Default exporter is no-op, not OTLP.** Spans are recorded inside the SDK so the orchestration code path never branches on "is observability on" — but with no `THALYN_OTEL_OTLP_ENDPOINT` set, no exporter is attached, and nothing leaves the machine. Setting the env var to a Langfuse OTLP endpoint flips the OTLP/HTTP exporter on.
- **Three span shapes, not one per concern.** `agent.run` (run-level), `<provider>.<operation>` (LLM call, e.g. `anthropic.chat`), `tool.<name>` (agent tool call), and `node.<name>` (orchestration node) are the four contexts. Sub-agent spawns nest naturally because each spawned LangGraph run gets its own `agent.run` span as a child of the parent's.
- **`set_tracer_provider` is once-only.** OTel's runtime only respects the first call. Tests attach an in-memory exporter via `add_span_processor` on the live provider rather than rebuilding it; the helper is shipped publicly so future test files can do the same.

Sub-agent run-id propagation through to OTel context happens automatically — the parent's `agent.run` span is current when `Runner.run` opens the child's, so the child's span inherits the trace id without any extra wiring.
