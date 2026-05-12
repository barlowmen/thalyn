# Thalyn — user guide

A practical walkthrough for launching the desktop app and using its
core surfaces. Aimed at someone running Thalyn from source on macOS;
Linux and Windows paths are noted where they differ.

## 1. First-time setup (~5 minutes)

You need:

- **Rust stable** — `curl https://sh.rustup.rs | sh`
- **Node.js ≥ 22** with **pnpm ≥ 10** — `corepack enable pnpm`
- **uv** for the Python sidecar — `brew install uv` (or see [astral.sh/uv](https://astral.sh/uv))
- **Tauri prerequisites** for your OS — [v2.tauri.app/start/prerequisites](https://v2.tauri.app/start/prerequisites/)
- A **Chromium-family browser** installed (Chrome, Brave, Edge, or Chromium itself) — only needed if you want to use the agent-driven browser surface
- An **Anthropic API key** if you want to use the default cloud brain;
  optional if you only plan to use a local model

In the repo root:

```sh
pnpm install
( cd brain && uv sync )
pnpm tauri dev
```

The first `pnpm tauri dev` is slow (Rust compiles the world). Subsequent
launches are quick.

## 2. The shell, top to bottom

When the window opens, you'll see three columns:

| Column | What's there |
|---|---|
| **Activity rail** (far left) | One icon per surface: chat, editor, terminal, browser, email, agents, memory, schedules, logs, connectors, settings. The bottom holds the theme toggle. |
| **Sidebar** | Per-surface context (project, runs, schedules, memory). Resizable. |
| **Main panel** | The active surface — chat by default. |
| **Inspector** (far right) | Live state of the active run: plan tree, action log, drift score, budget, sub-agents. Resizable. |

Press **Cmd-K** anywhere to open the command palette. Every action
you can take in the menus is also addressable from here.

## 3. Configure your brain

1. Click the **Settings** icon at the bottom of the activity rail (or
   the gear in the chat header).
2. Under **Providers**, paste an Anthropic API key. The key lands in
   your OS keychain — never in a config file.
3. Optional: under **Local providers**, point at your Ollama server
   (defaults to `http://localhost:11434`) so you can hot-swap to
   Qwen3-Coder for sensitive or offline work. The current provider is
   shown as a pill in the top-right of the chat surface; click it to
   switch.

Restart Thalyn after pasting the key — the brain reads it from an
environment variable forwarded at spawn.

## 4. Have a conversation with the brain

Click the **Chat** icon (default surface). Type a prompt; press Enter.

For anything that involves "do something" (refactor, write a test,
read a file), the brain spawns a **plan**. The plan tree appears in
the inspector, with per-step rationale and an estimated cost.

Approve, edit, or reject from the bar at the bottom of the inspector.
Approval is the primary safety gate — once approved, the agent
executes against the plan, and any meaningful drift is flagged.

While the agent runs, you can keep chatting with the brain — it
manages the work in the background and reports completions back into
the chat.

## 5. The other surfaces

### Editor

Click **Editor** in the activity rail. Open a file from the sidebar.
The Monaco editor with TypeScript and Python LSP support appears.
Pause typing for a moment in a spot that warrants a suggestion to see
ghost-text from the brain.

### Terminal

Click **Terminal**. A fresh xterm.js session opens, backed by a real
PTY. Run any shell command. Agents can attach to the same session and
observe the output for context.

### Browser

Click **Browser**, then **Start**. Thalyn discovers your Chromium
binary, spawns a headed instance with a per-Thalyn profile, and the
agent connects to it via the Chrome DevTools Protocol. Ask the brain
to "navigate to example.com and tell me the title" — it'll drive the
window and you'll see it happen.

### Email

Click **Email**. If no accounts are configured, you'll see an empty
state pointing you at Settings → Email accounts. Add an account:

1. Pick **Gmail** or **Microsoft Graph**.
2. Give it a label (e.g. "Personal") and the email address.
3. Mint an OAuth refresh token from your own Google Cloud / Microsoft
   Entra app. Paste it, the OAuth client ID, and (for Gmail or
   confidential Microsoft apps) the client secret.

Tokens land in your OS keychain. The next inbox fetch will exchange
the refresh token for an access token and pull recent messages.

**Send is double-gated.** Compose a message → **Prepare to send** →
the confirm modal appears → click **Send**. The brain refuses any
send that hasn't gone through this flow, even if you ask the agent to
do it autonomously.

### Connectors (MCP)

Settings → Connectors lists the first-party catalog (Slack, Microsoft
Office, Google Calendar) plus anything you've installed.

To use one:

1. Click **Install**.
2. Paste the per-connector secrets (e.g. Slack bot token + workspace
   ID, or Google Calendar OAuth refresh token + client credentials).
3. Toggle individual tools on under **Tools** — sensitive tools (post
   a Slack message, create a calendar event) are revoked by default.
4. Click **Start**. Thalyn spawns or connects to the MCP server and
   the agent can now call the granted tools.

### Memory

Click **Memory** in the sidebar. The brain's memory store lives here:
preferences, facts, references, and feedback the user (or the agent,
with a visible audit trail) has saved. Edit, delete, or add entries
freely; changes apply on the next agent turn.

Project-level memory: drop a `THALYN.md` (or `CLAUDE.md`) at the root
of any project and it'll be loaded into agent context whenever you
chat in that workspace.

### Schedules

Click **Schedules**. Create a recurring run with natural language
("every weekday at 6 a.m., summarize new GitHub issues") or paste a
cron expression for power users. The OS scheduler wakes Thalyn at the
right time; the brain runs the agent; sleep prevention keeps the
machine awake during the run (the display is allowed to power off).

### Sub-agents

When the brain spawns a sub-agent, a status card appears in chat and
a tile in the inspector. Click the tile to **open** the sub-agent —
its plan, action log, file diff, and browser DOM (if any) load in
the main panel. From there:

- **Take over** to pause it and chat directly with the sub-agent (rare path).
- **Hand back** when you're done — the sub-agent resumes with any
  guidance you added.
- **Kill** to stop it cold.

## 6. Observability (optional)

Thalyn ships zero telemetry by default. To watch agent runs live:

1. Bring up a self-hosted Langfuse:
   `docker compose -f observability/docker-compose.yml up -d`.
2. In Settings → Observability, paste the Langfuse OTLP endpoint
   (typically `http://localhost:3000/api/public/otel`).
3. Restart. New runs will ship OpenTelemetry GenAI spans to your
   Langfuse instance. None of this leaves your machine.

For crash reporting, paste your own Sentry DSN in the same panel.
Crashes go to *your* Sentry project; Thalyn never sees them.

## 7. Where the data lives

| What | Where (macOS) |
|---|---|
| App database (`app.db`: runs, schedules, memory, connectors, email accounts) | `~/Library/Application Support/com.thalyn.dev/app.db` |
| Per-run checkpoints | `~/Library/Application Support/com.thalyn.dev/runs/{run_id}/` |
| Audit logs | `~/Library/Application Support/com.thalyn.dev/runs/{run_id}.log` |
| Browser profile | `~/Library/Application Support/com.thalyn.dev/chromium-profile/` |
| Secrets (API keys, OAuth tokens) | macOS Keychain entries prefixed `thalyn:` |

To wipe everything: quit Thalyn, delete the `com.thalyn.dev` directory,
and remove the `thalyn:*` keychain entries.

## 8. Common keystrokes

| Keys | Action |
|---|---|
| Cmd-K | Command palette |
| Cmd-, | Settings |
| Cmd-Enter | Send a chat message |
| Esc | Close the active modal or palette |

## 9. Getting unstuck

| Symptom | Try |
|---|---|
| The brain badge says "starting" forever | Quit and relaunch. The Python sidecar may have crashed; the supervisor should restart it on next launch. Check `pnpm tauri dev` console output. |
| Chat returns "no provider configured" | Settings → Providers — paste an API key, then restart. |
| Email account just says "no refresh token configured" | Settings → Email accounts → paste the refresh token + client id. The brain reads them at the moment of the next email RPC. |
| MCP connector won't start | Check that `npx` is on your PATH and the upstream MCP server package can be installed. Errors surface inline on the connector card. |
| Browser surface says "no Chromium found" | Install Chrome, Brave, Edge, or Chromium and restart Thalyn — discovery runs at lifecycle-start. |

If you're well and truly stuck, open an issue with the symptoms, the
last 50 lines of the `pnpm tauri dev` console, and your hypothesis.
