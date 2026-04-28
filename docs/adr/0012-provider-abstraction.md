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

### Refinement at v0.10 implementation

v0.10 lit up the local-provider path. The single-`stream_chat`
shape held; three implementation patterns are worth recording so
future adapters follow the same shape:

- **Lazy import for platform-specific dependencies.** `MlxProvider`
  imports `mlx_lm` lazily inside the call path. Hosts without
  Apple Silicon (Linux, Windows, Intel Macs) get a clean
  `ChatErrorChunk` instead of a hard import-time crash, so the
  same packaged binary still boots — the user just can't
  *select* the MLX provider on those hosts. Future
  platform-specific adapters should follow the same pattern.
- **Per-provider tool-call reliability tier.** `CapabilityProfile`
  carries `tool_use_reliability: ReliabilityTier`
  (`high` / `medium` / `low` / `unknown`). The orchestrator
  reads this when deciding whether to require provider routing
  for tool-heavy plans — Anthropic is `high`, Ollama with
  Qwen3-Coder-Next is `medium` (the 2026 ecosystem went through
  several tool-call format issues that Unsloth fixed, but the
  reliability still trails frontier-cloud models).
- **Ollama context-window override.** Ollama defaults to a
  4096-token context; Qwen3-Coder-Next supports up to 256 K.
  We set the context size explicitly when spawning a model,
  rather than relying on the Ollama default — otherwise long
  plans truncate silently mid-stream.

The Rust trait still holds the metadata-only surface; the brain
provider registry now has five concrete adapters (Anthropic,
Ollama, MLX) plus two placeholders (OpenAI-compatible,
llama.cpp) that surface a clean error on selection. The
`ToolCallNormalizer` slot is still empty — Ollama and MLX both
return tool calls in the same normalized chunk shape because the
adapter does the translation in `_normalize_message`. A
dedicated normalizer module lands when a provider's tool-call
output diverges enough that the per-adapter logic stops being
the right home.

### Refinement: auth-backend split (see ADR-0020)

The provider abstraction is split into two composed traits: the
existing `LlmProvider` (capability + streaming) and a new
`AuthBackend` (probe + ensure-ready + `token()`). A single
`AnthropicProvider` class composes either `ClaudeSubscriptionAuth`
or `AnthropicApiAuth` and chooses what (if anything) to put in the
SDK's spawn env at call time. The auth backend dimension is
distinct from the model dimension per `02-architecture.md` §7.1,
and the v1 default flips from API-key paste to Claude subscription.
Detail in ADR-0020; spike in `docs/spikes/2026-04-28-claude-cli-auth.md`.
