# Thalyn

Thalyn is a single desktop application that wraps a developer's entire
workflow around a central agent. You converse with one orchestrator —
the brain — and it spawns, supervises, and merges work from specialist
sub-agents. The brain defaults to Claude (via the Claude Agent SDK) and
can be hot-swapped to a local model for sensitive or offline work. The
goal: editor, terminal, browser, email, productivity connectors, and the
agent control plane in one self-contained desktop app, with sandboxing
and drift monitoring that make unattended runs defensible.

> **Status:** pre-alpha, single-developer scope. The desktop app
> builds and runs end-to-end on macOS today: chat with Claude (or a
> local model via Ollama) flows through a LangGraph orchestrator with
> plan approval, drift monitoring, and sub-agents; an embedded Monaco
> editor, an xterm.js terminal, a sidecar Chromium for browser-use,
> a connector marketplace for MCP servers, and an inbox surface for
> Gmail / Microsoft Graph all sit beside the chat. The `docs/going-public-checklist.md`
> enumerates what still has to land before this is safe to install for
> anyone other than its developer.

## What this is, in one paragraph

The market is full of VS Code forks (Cursor, Windsurf, Cline) and cloud
agent sandboxes (Devin, Replit Agent). None of them give you "central
brain orchestrating sub-agents inside one self-contained desktop app,
with the brain swappable for a local model." That's the gap Thalyn
fills.

The full thesis lives in [`01-requirements.md`](01-requirements.md);
the system architecture lives in
[`02-architecture.md`](02-architecture.md); the build plan that
sequences the work lives in [``]();
ADRs for individual technology choices live in
[`docs/adr/`](docs/adr/).

## Architecture, briefly

```
┌────────────────────────┐    ┌──────────────────────┐
│  Tauri main (Rust)     │    │  Brain sidecar       │
│  ──────────────────    │    │  (Python, async)     │
│  WebView (React)       │◀──▶│  JSON-RPC dispatch   │
│  Sidecar supervisor    │    │  → orchestration     │
│  Provider abstraction  │    │    (LangGraph +      │
│  Sandbox manager       │    │     Claude SDK)      │
│  IPC broker            │    │                      │
└────────────────────────┘    └──────────────────────┘
        │                                  ▲
        │ stdin/stdout (NDJSON +           │
        │   JSON-RPC 2.0)                  │
        └──────────────────────────────────┘
```

For details, see [`02-architecture.md`](02-architecture.md).

## Configuring things by asking

Every configurable surface in Thalyn is reachable conversationally —
connectors, worker routing, personal-memory entries, project flags,
schedules, themes. You ask Thalyn in the eternal chat ("set up Slack",
"route coding to ollama in this project", "remember that I prefer
atomic commits"), Thalyn walks the inputs, surfaces an in-app browser
drawer for OAuth, and confirms when it lands. Actions that change the
world on your behalf — send a message, publish a doc — still surface
the per-action approval dialog before they fire. Settings panels exist
for the same surfaces; the conversational path is the recommended one.

## Running from source

You will need:

- **Rust** stable (install with `curl https://sh.rustup.rs | sh`).
- **Node.js** ≥ 22 with **pnpm** ≥ 10 (`corepack enable pnpm`).
- **uv** for the Python sidecar (`brew install uv` on macOS, or see
  [astral.sh/uv](https://astral.sh/uv) for other platforms).
- Platform build deps for Tauri:
  [the Tauri prerequisites guide](https://v2.tauri.app/start/prerequisites/).

Then:

```sh
pnpm install
( cd brain && uv sync )
pnpm tauri dev
```

This launches the desktop app. Click **Ping brain**: a JSON-RPC `ping`
travels from the renderer through the Rust core, into the Python
sidecar, and back. The latency is shown alongside the version stamp.

## Local checks

The same gates that run in CI:

```sh
# Frontend
pnpm exec tsc --noEmit
pnpm build

# Rust core
( cd src-tauri && cargo fmt --check && cargo clippy --all-targets -- -D warnings && cargo test --lib )

# Python sidecar
( cd brain && uv run ruff check && uv run ruff format --check && uv run mypy && uv run pytest )

# Internal-workflow leakage in the staged diff
scripts/scan-leakage.sh
```

To wire the leakage scan into git as a pre-commit hook, install
[`pre-commit`](https://pre-commit.com) once (`uv tool install
pre-commit`) and run `pre-commit install` in the repo.

## Layout

| Path | What it is |
|---|---|
| `src/`, `index.html`, `vite.config.ts` | React renderer, served by Vite |
| `src-tauri/` | Rust core: window, sidecar supervisor, IPC, commands |
| `brain/` | Python sidecar (JSON-RPC dispatcher, agent runtime) |
| `scripts/` | Repo-wide tooling, including the leakage scanner |
| `docs/adr/` | Architecture Decision Records (MADR) |
| `docs/going-public-checklist.md` | Hardening list gating any public release |
| `01-requirements.md` | Product spec |
| `02-architecture.md` | System architecture |
| `` | Build plan |

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md). Short version: Conventional
Commits, every commit goes through the gate sequence, no internal-workflow
language in commit messages or code, and the leakage scanner enforces it.

## Security

Threat model and reporting live in [`SECURITY.md`](SECURITY.md). The
[`going-public-checklist`](docs/going-public-checklist.md) enumerates
the hardening pass that gates any release for users who aren't the
developer.

## License

[MIT](LICENSE). The license decision will be revisited before public
release per the going-public checklist.
