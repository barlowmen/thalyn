# ADR-0020 — Brain auth-backend split: Claude subscription default, API-key secondary

- **Status:** Proposed
- **Date:** 2026-04-28
- **Deciders:** Barlow
- **Supersedes:** —
- **Refines:** ADR-0012 (provider abstraction)

## Context

The v1 build asked for an Anthropic API key on first run. This is wrong
in two ways for the v2 user:

1. **The default user is a Claude subscriber.** Asking for an API key
   on top of the subscription is duplicate billing — the user pays
   twice for the same Claude access — and friction the v2 first-run
   target (≤ 90 s to first conversation per `01-requirements.md` §F9.2)
   cannot absorb.
2. **The "auth backend" and "model" dimensions were collapsed in v1.**
   ADR-0012 named "Anthropic" as a single provider, but in v2
   `claude_subscription` and `anthropic_api` are two *credential
   sources* fronting the same Anthropic models. They differ only in
   how the SDK is authorized; the call path, the streaming shape,
   and the capability profile are identical. v2's first-run flow
   (`01-requirements.md` §F9.1) needs them as a first-class dimension
   in the UI so the user can pick the subscription path without
   confronting a key-paste step.

The Claude Agent SDK already shells out to the bundled `claude` CLI
for every call (see `docs/spikes/2026-04-28-claude-cli-auth.md`); the
CLI's stored OAuth token is what flows through when no
`ANTHROPIC_API_KEY` is set. So the architectural split has *zero
runtime cost* — the same `AnthropicProvider` works under both auth
modes, distinguished only by what it puts in the SDK's spawn env.

`02-architecture.md` §7.1 already names the two-dimensional shape and
§7.2/§7.3 sketch the trait + Protocol. This ADR records the decision
explicitly so the refactor can land with a referenceable rationale,
and so future provider additions (Bedrock, Vertex, Azure) inherit the
same shape without re-litigating it.

## Decision

**1. Auth backend is a runtime trait separate from the provider trait.**
A new `AuthBackend` Rust trait and Python `Protocol` carry probe,
ensure-ready, and (Python only) `token()` semantics. The provider
keeps its capability/streaming surface; the auth backend is composed
into it at construction time.

**2. The trait carries six kinds, matching the persistence enum.**
`claude_subscription`, `anthropic_api`, `openai_compat`, `ollama`,
`llama_cpp`, `mlx`. The values match the `auth_backends.kind` SQLite
column (migration 003) and the Rust serde snake_case rendering, so
the wire shape is symmetric across the three layers (storage, IPC,
runtime).

**3. The Anthropic split-provider is a single class composing one of
two `AuthBackend`s.** `AnthropicProvider(auth_backend: AuthBackend)`
chooses subscription or API-key auth at construction time. Selecting
`ClaudeSubscriptionAuth` produces a provider that calls the SDK with
**no** `ANTHROPIC_API_KEY` in env (the bundled CLI uses its OAuth
token); selecting `AnthropicApiAuth` produces one that injects the
key into `ClaudeAgentOptions(env=...)`. Tool-call shape, capability
profile, and streaming events are identical in both cases.

**4. Claude subscription is the v1 default.** First-run probes
`claude auth status --json` (see the spike) and, on
`loggedIn: true`, surfaces the subscription option as
recommended-and-pre-selected. The API-key path remains in the
provider list but as the secondary option. If the CLI is not
detected or not authenticated, the wizard falls through to the
existing API-key paste flow.

**5. Auth-backend selection is hot-swappable.** The provider switcher's
existing capability-delta dialog (ADR-0012, F4.5) is extended to also
fire when the *auth backend* changes inside the same provider kind
(e.g. subscription → API key on the same Anthropic adapter). The
delta is informational in that case (no capability change), but the
dialog acts as the user-facing "you're about to switch credentials"
confirmation.

**6. `token()` is the single point of variance.** Every adapter
returns either a string (set as the credential env var by the
provider) or `None` (the auth backend manages credentials out of
band — the subscription case, plus the local backends that don't
need a credential at all). This collapses the auth dimension into
one wire-level decision and keeps the provider class fully
auth-agnostic above that line.

## Consequences

- **Positive.** First-run flow can drop the user past the API-key
  paste step in the common case; the `(auth × model)` matrix is
  a real dimension of the data model rather than an implicit
  convention; future providers (Bedrock, Vertex, Azure) inherit the
  pattern as a one-adapter-per-credential-source addition; the
  `auth_backends` table (migration 003) becomes the operational
  store the runtime actually reads, not just a reservation.
- **Negative.** A second runtime trait increases the surface area of
  the provider abstraction. Mitigation: the trait is intentionally
  narrow (three async methods on the Python side, two on Rust); the
  capability-profile / chat-chunk types still live on `LlmProvider`;
  composition is `AnthropicProvider(auth_backend=...)` which mirrors
  the Rust `Box<dyn AuthBackend>` shape, so the cognitive overhead
  is bounded.
- **Neutral.** The `claude` CLI's OAuth refresh rhythm is delegated
  to the CLI itself; the SDK reports stale-token errors as ordinary
  provider errors which the UI already surfaces. We do not own the
  refresh schedule.

## Alternatives considered

- **One provider class per auth backend (separate
  `AnthropicSubscriptionProvider` and `AnthropicApiProvider`).**
  Rejected. Would duplicate the Anthropic adapter's tool-call
  normalization, capability profile, model defaults, and streaming
  logic in two places. The two backends differ only in spawn env;
  splitting the *whole provider* is a larger surface than the
  difference warrants.
- **Encode auth as an extra `kind` value on the existing
  `ProviderKind` enum.** Rejected. Conflates the credential source
  with the model family, which is exactly the v1 mistake. A user
  switching from subscription to API key is keeping the same model
  family; the kind shouldn't change.
- **Lift auth into the renderer entirely (Tauri commands directly
  manage the env / keychain, no Python-side trait).** Rejected.
  The auth decision needs to be inspectable per call inside the
  brain (e.g. for re-prompting on stale-token errors) and per
  agent-tier (each tier may eventually have its own auth backend
  per F4.3). Centralizing in the brain keeps the per-tier surface
  available without a renderer round-trip.

## Notes

The spike (`docs/spikes/2026-04-28-claude-cli-auth.md`) records the
underlying SDK research. Per-call probe caching, CLI-found-but-stale
recovery, and the in-app `claude /login` UX are deferred:
caching is per-instance with explicit-invalidation semantics; stale
errors surface through the existing `ChatErrorChunk` path; in-app
login lands with the drawer system (later phase) once the terminal
surface is real.

## Addendum (2026-04-28, post-v0.22 architecture review)

The first v2 architecture review surfaced a wrinkle worth recording on
this ADR explicitly. **Anthropic enforced a policy in early April
2026 that the Claude Agent SDK requires API-key auth and explicitly
prohibits Pro/Max subscription billing.** The bundled `claude` CLI
itself still authenticates fine against subscription auth (it's the
user's own client of the Claude product, not the SDK), so the
``ClaudeSubscriptionAuth`` flow we ship in v0.22 works at the CLI
layer — `claude auth status` reports `loggedIn: true` and the bundled
binary delegates calls to whatever Claude product the user is signed
into.

The implication is a credential-source / billing-target distinction:

- The user's choice between subscription and API key is about
  *credential source* — what the SDK uses to authenticate calls.
  ``ClaudeSubscriptionAuth.token()`` returns ``None`` so the bundled
  CLI uses its OAuth state; ``AnthropicApiAuth.token()`` returns the
  pasted key so the SDK uses an explicit credential.
- Whether Claude API costs hit Pro/Max billing or burn API credit
  depends on Anthropic's accounting, which is outside Thalyn's
  control.

The user-visible UX in v0.22 is unchanged (the wizard's Claude
subscription option still works); the subtlety belongs in product
docs and the capability-delta dialog's explanatory copy as that
surface is iterated. The ADR's core decision — split auth from
provider, default to subscription credentials — stands.

Reference: `docs/architecture-reviews/2026-04-28-v22.md` § ADR-0020.
[Anthropic Agent SDK overview](https://platform.claude.com/docs/en/agent-sdk/overview),
[Issue #559 (Max-plan billing)](https://github.com/anthropics/claude-agent-sdk-python/issues/559).
