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
