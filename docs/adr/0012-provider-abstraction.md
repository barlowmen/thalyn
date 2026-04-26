# ADR-0012 — Provider abstraction: in-process trait + per-provider adapter

- **Status:** Accepted (provisional)
- **Date:** 2026-04-25

## Context

The brain and every sub-agent must reach an LLM through a uniform interface so providers (Anthropic, OpenAI-compatible, Ollama, llama.cpp, MLX) are swappable per-agent (`01-requirements.md` F3.2). Tool/function-calling shapes differ across providers and must be normalized so the orchestrator never sees provider-specific schemas. Latency matters; an external gateway adds a hop.

## Decision

A **Rust trait `LlmProvider`** in the core (for capability probing, model listing) and a **Python `LlmProvider` Protocol** in the brain (where most LLM traffic lives). Both share a JSON capability-profile schema. Adapters in v1: `AnthropicProvider` (delegates through Claude Agent SDK), `OpenAICompatibleProvider`, `OllamaProvider`, `LlamaCppProvider`, `MlxProvider`. A `ToolCallNormalizer` translates tool-call shapes so the orchestrator only ever sees a normalized form.

We **do not** route through an external gateway (LiteLLM as a separate process). LiteLLM-style normalization happens inside our own provider abstraction.

## Consequences

- **Positive.** No extra process hop. We control tool-call translation per provider — important because tool-use reliability varies. Capability profiles drive the UI's "capability delta" warning when the user swaps providers (F3.4). Adding a new provider is an adapter, not a refactor.
- **Negative.** We own the maintenance of every adapter. Provider API drift is on us. Mitigation: pin SDK versions, regression-test adapter behavior on each upgrade.
- **Neutral.** An optional LiteLLM-as-process mode could be added later for users who want to point Thalyn at their existing LiteLLM gateway.

## Alternatives considered

- **LiteLLM as in-process gateway library.** Considered; rejected because we want tighter control over tool-call translation than a generic gateway provides.
- **Anthropic-only.** Rejected; the swappable-provider promise is core to Thalyn's positioning (F3).
- **OpenAI-shape only, with adapters that translate to it.** Rejected because Claude's tool-use idioms are richer and we'd lose fidelity.

## Notes

Adapter test suites use recorded fixtures (VCR-style) so we can re-run against captured provider responses without burning credits.

### Refinement at v0.3 implementation

Two small departures from the sketch landed when the abstraction met working code; the architectural decision in **Decision** above is unchanged.

- **Rust trait is narrower than the sketch.** Live LLM traffic flows through the Python brain, so the Rust trait dropped `complete` / `stream` / `embed` and kept the metadata surface — `id`, `display_name`, `capability_profile`, `supports`, `probe`. The Rust core stays out of the IPC hot path for completion calls; capability listing and reachability stay synchronous.
- **Python protocol uses a single `stream_chat`.** Single-turn streaming is the only call shape the brain needs for v0.3; chat history threading and embeddings stay deferred until the orchestration layer needs them. The chunk shape (`start` / `text` / `tool_call` / `tool_result` / `stop` / `error`) is the wire contract that JSON-RPC notifications carry to the renderer unchanged.
- **`ToolCallNormalizer` deferred.** With only the Anthropic adapter live there is nothing to normalize across; the slot lands when the second adapter ships.
