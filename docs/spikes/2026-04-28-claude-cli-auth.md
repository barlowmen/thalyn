---
date: 2026-04-28
risk: Phase v0.22 §Risks — does `claude_agent_sdk` expose a clean way to inherit the user's Claude subscription without an API key, or do we need a `claude --print` shim?
adr: 0012 (refined) + 0020 (proposed)
---

# Spike: claude CLI auth probe + SDK integration

- **Question.** v0.22 wants the user's Claude subscription to be the
  default brain auth, with no API-key paste step. The Claude Agent SDK
  is already a Python dependency. Does the SDK already inherit
  subscription auth from the `claude` CLI, or do we need to write a
  `claude --print` exchange shim? What's the contract for detecting
  "is the CLI authenticated" without spawning a session?
- **Time-box.** 4 h nominal. **Actual:** ~30 min. The SDK already does
  the right thing; the only question was the probe shape, which the
  CLI exposes natively as `claude auth status --json`.
- **Outcome.** Answered. The SDK already shells out to the `claude`
  binary and inherits whatever auth that binary holds; no shim
  needed. The detection contract is `claude auth status --json`,
  which returns a stable JSON shape with `loggedIn`, `authMethod`,
  and `apiProvider`. Toggle between subscription and API-key auth is
  the presence/absence of `ANTHROPIC_API_KEY` in the spawn env.

## Findings

### 1. The SDK delegates to the `claude` CLI for transport and auth

`claude_agent_sdk._internal.transport.subprocess_cli.SubprocessCLITransport`
is the only transport. It locates the `claude` binary in this order
(at `_find_cli`, lines 63–84 of the installed package):

1. **Bundled CLI** (`claude_agent_sdk/_bundled/claude`) — the SDK
   wheel ships a CLI binary, so the user almost always has *some*
   working CLI even without a separate install.
2. `shutil.which("claude")` on `$PATH`.
3. Common install locations (`~/.npm-global/bin/claude`,
   `/usr/local/bin/claude`, `~/.local/bin/claude`, etc.).

Once found, the SDK spawns the binary with
`--output-format stream-json --verbose --input-format stream-json`
and pipes a stream of user messages over stdin, reading streamed
events on stdout. Auth is whatever the spawned CLI process resolves
on its own. The SDK never reads keychain entries directly.

**Implication.** We never need to build a `claude --print` shim. The
SDK *is* a `claude` shim already; we just need to put the right env
in front of it.

### 2. `claude auth status --json` is the canonical probe

```text
$ claude auth status --json
{
  "loggedIn": true,
  "authMethod": "oauth_token",
  "apiProvider": "firstParty"
}
```

- `loggedIn: bool` — the deciding flag for "subscription auth is
  available."
- `authMethod` — `"oauth_token"` for subscription / setup-token
  flows, `"api_key"` for API-key flows. (Distinguishing these
  matters because we want to *prefer* the subscription path even if
  an API key is also present.)
- `apiProvider` — `"firstParty"` for direct Anthropic, other values
  for Bedrock / Vertex / Foundry.

The `--json` flag is the default (text output requires `--text`), so
the contract is stable.

### 3. The auth toggle is `ANTHROPIC_API_KEY` in the spawn env

Per `claude_agent_sdk/_internal/session_resume.py:334`:

```python
opt_env.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
```

The CLI itself follows the same precedence — env var wins. So:

- **Subscription auth.** Spawn the CLI with **no** `ANTHROPIC_API_KEY`
  in env. The CLI uses its stored OAuth refresh token. (Refresh is
  handled inside the CLI; we do not see or manage it.)
- **API-key auth.** Spawn with `ANTHROPIC_API_KEY=<key>`. The CLI
  uses the key.

The current v1 brain wires the env var from the OS keychain into the
brain subprocess's spawn env (`src-tauri/src/lib.rs:1419-1423`). For
the auth split, the per-call decision moves into the
`AnthropicProvider`: it builds `ClaudeAgentOptions(env={...})` either
with or without `ANTHROPIC_API_KEY` based on the active
`AuthBackend`. The brain process itself no longer needs to inherit
the key — only the SDK call does.

### 4. The bundled CLI changes our distribution story

Because `claude_agent_sdk` ships a bundled binary, *every* Thalyn
install has a working `claude` CLI even without a separate
`npm install -g @anthropic-ai/claude-code`. That makes the
"detect & invite the user to log in" flow practical end-to-end:

1. Probe: `<bundled-or-system claude> auth status --json`.
2. If `loggedIn: false`, surface "log in to Claude" — which can use
   the same bundled binary's `claude /login` flow (deferred to the
   first-run wizard's "log me in" affordance, scoped within v0.22).
3. If `loggedIn: true`, drop the user past the API-key paste step.

We resolve the same path the SDK does (bundled first, then PATH,
then common locations) so the probe and the runtime use the same
binary.

## Decision implications

- **No shim.** The auth split is purely a Python/Rust trait + a thin
  probe utility around `claude auth status --json`.
- **Auth backend selects env, not endpoint.** Both
  `ClaudeSubscriptionAuth` and `AnthropicApiAuth` route through the
  same `AnthropicProvider` and the same `ClaudeSDKClient`; they
  differ only in what `ClaudeAgentOptions(env=...)` carries.
- **`ClaudeSubscriptionAuth.token()` is a no-op.** The SDK reads
  the CLI's stored token internally. The Protocol's `token()` method
  becomes "the thing to inject into env, if any" — for subscription
  it's `None`; for API key it's the secret string.
- **Probe caching.** `claude auth status` is fast (< 200 ms locally)
  but spawning a subprocess on every chat turn is wasteful. Cache the
  result on the auth backend instance, invalidate on configuration
  change (the architecture-doc pointer at §7 already names this).
- **Error shape.** Three failure modes the probe must distinguish:
  CLI not found (`CLINotFoundError`-equivalent), CLI found but not
  authenticated (`loggedIn: false`), CLI authenticated but stale
  token (`claude` itself surfaces this on first call). The first two
  are pre-call probe outcomes; the third is a runtime error from the
  SDK and gets wrapped into a `ProviderError` like any other.

## Out of scope for v0.22

- **OAuth login flow inside the app.** `claude /login` is interactive
  TTY-driven. Wrapping it in the renderer is a v0.22 stretch; the
  fallback the wizard ships is *"open a terminal drawer and run
  `claude /login` there."* In-app OAuth lands in v0.27 (the drawer
  system, where the terminal is a real surface) or later.
- **Setup-token path.** `claude setup-token` produces a long-lived
  token usable from CI / non-interactive contexts. v0.22 only needs
  the day-to-day subscription flow; setup tokens are a v1.0 polish.
- **Refresh-rhythm probe.** The architecture doc flags the
  subscription token's refresh rhythm as a risk; the SDK delegates
  refresh to the CLI, and the CLI manages it transparently. We
  surface stale-token errors from the SDK as a "log in again" prompt
  in the wizard, but we don't try to read or refresh the token
  ourselves.
