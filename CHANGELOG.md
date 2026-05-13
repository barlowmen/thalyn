# Changelog
## 0.38 — 2026-05-13

### Documentation

- Add visual-identity, perf, and a11y rows for v1 release

### Features

- Split status tokens so text-danger clears WCAG 4.5:1 on dark

### Design

- Ship the locked gapped-T sigil across every platform
## 0.37 — 2026-05-13

### Documentation

- Stop referencing the build-plan doc in public surfaces
- Reframe for public source visibility
- Reframe for the source-public, app-still-private split
- Set external-contributor expectations + name the scanner tokens
- Ratify the repo-public flip with source-not-binary scoping
- Soften the cadence note to match "PRs welcome, slow review"
- Route disclosure through github private advisories
- Drop dangling architecture-doc link, point at ADRs instead
- Add ci supply-chain hardening items
- Propose ADR-0027 generalising drift across the EM hierarchy
- Refine ADR-0009 with provenance fields on MEMORY_ENTRY
- Promote ADR-0027 to Accepted

### Features

- Add a mode-parameterised info-flow audit primitive
- Audit lead replies through the reported_vs_truth hop
- Audit the brain-to-user relay alongside the lead report
- Surface a confidence pill on delegated replies
- Route confidence-pill drill clicks into the lead-chat drawer
## 0.36 — 2026-05-12

### Documentation

- Fifth v2 cycle, post-v0.36

### Features

- Spawn persistent sub-leads under an active project lead
- Isolate sub-lead memory by agent_id namespacing
- Address sub-leads in chat with attribution chain
- Conversational sub-lead spawn through the action registry
## 0.35 — 2026-05-12

### Documentation

- Land ADR-0024 (project mobility + pluggable classifier)
- Promote ADR-0024 to Accepted

### Features

- Plan project merges with a pure-read dry-run
- Apply project merges transactionally with NDJSON audit
- Wire project.merge through IPC with plan/apply shape
- Make project merge conversational through the action registry
- Compose classifiers with deterministic-beats-LLM precedence
- Surface new-project suggestions through thread.send
## 0.34 — 2026-05-11

### Documentation

- Teach users + Thalyn that config lives in the chat

### Features

- Introduce the action registry primitive
- Land the memory.remember conversational action
- Wire connector setup through the action registry
- Stage hard-gate actions for explicit user approval
- Ask for missing inputs instead of failing the match
- Fold action summaries into the per-turn context

### Refactoring

- Host routing edits in the action registry
## 0.33 — 2026-05-11

### Bug Fixes

- Link libgomp on linux when whisper is enabled
- Use Thalyn instead of "the brain" in MLX disclosure

### Documentation

- Re-run bake-off on M4 with synthetic delegation fixture
- Promote ADR-0025 to Accepted with measured M4 numbers
- Fourth v2 cycle, post-v0.33

### Features

- Scaffold the STT bridge seam
- Land the local Whisper engine
- Add the model store and pick the best on-disk model
- Preload base.en in the bundle and resolve it at runtime
- Wire cpal mic capture and composer push-to-talk
- Stream interim transcripts via WhisperStream
- Emit per-chunk peak amplitude on stt:level
- Merge THALYN.md identifiers with memory in voice vocabulary
- Render interim transcripts live during push-to-talk
- Add continuous-listen mode with simple-VAD utterance segmentation
- Expose continuous-listen mode + mic gesture in settings
- Wire Deepgram cloud STT opt-in skeleton
- Wire MLX-Whisper opt-in skeleton
- Surface mic-permission denial with OS settings deep-link

### Ci

- Install libasound2-dev for the voice-whisper cpal build
## 0.32 — 2026-05-04

### Documentation

- Split voice into a research spike + an implementation phase
- Land the voice-integration spike with engine + cloud picks
## 0.31 — 2026-05-03

### Features

- Add lifecycle CRUD and project.* RPC surface
- Route turns through a pluggable project classifier
- Recognise @Lead-X mentions mid-message
- Replace the project pill with the multi-project switcher
- Per-project breakdown + project-tag pills on chat turns
## 0.30 — 2026-05-02

### Bug Fixes

- Point beforeBundleCommand at the repo-root path tauri uses
- Synthesize CefAppProtocol so the in-process swizzle wires
- Use the right cef_window_handle_t shape per platform
- Enable the DevTools server via the command-line switch

### Documentation

- Commit to runtime swizzle for in-process CEF tao integration
- Accept ADR-0029 and clarify the runtime-swizzle ivar path
- Scope the macOS CEF helper-bundle integration
- Refine helper-bundle recommendation after tauri-bundler audit
- Scope cross-platform CEF parenting as cfg-gated stubs

### Features

- Add the runtime swizzle that grafts CEF protocols onto TaoApp
- Wire pre-Tauri framework load + setup-hook swizzle
- Produce macOS helper bundles via beforeBundleCommand
- Run CEF in-process from the Tauri setup hook
- Parent the in-process Browser to a Tauri-owned NSView
- Add a parent-process watchdog

### Refactoring

- Make the TaoApp swizzle genuinely all-or-nothing
- Retire the v1 system-Chromium sidecar

### Build

- Pin the app's default-run target so tauri build picks it
- Make the bundled CEF engine the default cargo build
- Produce a standalone sidecar via PyInstaller
- Stage the bundled brain inside the macOS .app
## 0.29 — 2026-04-30

### Documentation

- Ratify cef-rs engine swap and supersede sidecar Chromium
- File cef-macos-message-loop spike report
- Split engine swap into v0.29 child-binary + v0.30 in-process
- Unwind the engine-swap phase split into a single in-process ship

### Features

- Scaffold CEF lifecycle owner with SDK + profile + port-file modules
- Scaffold thalyn-cef-host child binary with NSApplication subclass
- Spawn thalyn-cef-host from CefHost::start and surface the WS URL
- Route browser_* commands through the bundled-CEF host
- Add browser drawer surface with engine lifecycle chrome
- Plumb the drawer-host rect from renderer to CefSession

### Build

- Add optional cef feature pinned to CEF 147 with CI build
## 0.28 — 2026-04-29

### Documentation

- File the post-v0.28 cycle review

### Features

- Add worker drawer surface for plan + action-log inspection
- Route transient strip clicks to the worker drawer
- Render plan-approval gates inline in the conversation
- Add lead drawer surface with worker tiles + memory inspector
- Add direct lead-chat drawer with full eternal-thread parity
- Expose active leads in the command palette
- Emit lead.escalation when reply is question-dense (F2.5)

### Ci

- Reject retired Anthropic model ids in CI
## 0.27 — 2026-04-29

### Features

- Add drawer-host primitive + drawer-surface chrome
- Wire chat-first shell to drawer host + palette
- Retire legacy mosaic shell, promote ADR-0026 to Accepted
## 0.26 — 2026-04-29

### Documentation

- Land chat-first shell ADR and refine ADR-0013 layout claim

### Features

- Add chat-first top bar with brain badge and project pill
- Add transient progress strip primitive
- Add voice mic stub and roomy preset to composer
- Render day-dividers between messages crossing calendar days
- Land chat-first shell as default route, retain /legacy
## 0.25 — 2026-04-29

### Documentation

- Finalize ADR-0009 around the five-tier memory model
- File the post-v0.25 cycle review

### Features

- Rename memory user scope to personal and add episodic
- Pull personal memory into eternal-thread context
- Auto-load THALYN.md into the lead's session prompt
- Worker project-memory writes flow through the lead
- Add scope filter to the memory inspector
## 0.24 — 2026-04-29

### Documentation

- Draft ADR-0023 for worker model routing
- Accept ADR-0023 after routing layer ratifies the hierarchy

### Features

- Pure route_worker function with task-tag vocabulary
- Routing rpc backed by overrides store
- Route worker spawns through per-project routing layer
- Conversational edit path for worker routing
## 0.23 — 2026-04-29

### Bug Fixes

- Annotate provider_config dict in lead-lifecycle tests

### Documentation

- Accept ADR-0021 after v0.23 ratifies the hierarchy

### Features

- Persistent project-lead lifecycle rpc
- Delegate addressed turns to project lead
- Surface project leads in the agents inspector
- Tag runs and worker descendants under a project lead
- Drill into a lead's recent runs from the agents inspector
- Attribute delegated chat replies with a lead chip
## 0.22 — 2026-04-28

### Bug Fixes

- Drop unused type-ignore in auth-anthropic tests
- Bypass first-run wizard in visual specs
- Skip first-run wizard under navigator.webdriver

### Documentation

- Claude cli auth probe — sdk inherits, no shim needed
- First v2 architecture review (post-v0.22 cycle)

### Features

- Introduce AuthBackend trait + Protocol
- Claude-subscription and api-key auth-backend adapters
- Ollama, llama.cpp, mlx, openai-compat auth adapters
- Real auth.list / auth.probe / auth.set rpc handlers
- Hot-swap anthropic auth backend on auth.set
- Default thalyn identity in the eternal-thread system prompt
- Rename user-facing brain references to thalyn
- First-run wizard for brain selection
- Capability-delta surfaces auth-backend changes
- Day-divider since-we-last-spoke digest greeting

### Refactoring

- Compose AuthBackend into AnthropicProvider
## 0.21 — 2026-04-28

### Features

- Add status column and FTS5 episodic index for eternal-thread durability
- Atomic in-progress/completed turn pair + FTS-backed thread search
- Wire thread.recent / thread.search / digest.latest IPC handlers
- Land thread.send write path with persistence and recovery
- Rolling summarizer + idle trigger + second-level compression
## 0.20 — 2026-04-28

### Bug Fixes

- Drop redundant pnpm version override
- Darken accent + status colors for WCAG AA contrast
- WCAG AA contrast on coloured Badge tones + error alert
- Make shell resize handles discoverable
- Shell panels can grow as well as shrink
- Pass shell panel sizes as percentages, not raw numbers

### Documentation

- Import product specification and build plan
- Import architectural decision records
- Write initial README, CONTRIBUTING, and SECURITY
- Record the v0.3 provider-abstraction refinements
- Architecture review for the post-v0.3 cycle
- Architecture review for the post-v0.6 cycle
- Sandbox tier model + shell-allowlist reference
- Local-models recommendations, hardware floor, capability deltas
- Architecture review for the post-v0.12 cycle
- Retire browser-embedding architecture risk via spike
- Browser-pane reference
- Sandbox-tiers updated with all four tiers + escalation policy
- Architecture review for the post-v0.15 cycle
- User guide, what-to-test punch list, feedback template
- Lock app icon to gapped-T sigil
- Adopt bundled Chromium via cef-rs (supersedes ADR-0010)
- Rebase product, architecture, and build plan to v2 baseline
- Align stale build plan section refs with v2 numbering
- Adopt brain-owned SQLite storage (ADR-0028)
- Align architecture and build plan with brain-owned storage
- Draft ADR-0021 — agent hierarchy

### Features

- Minimal sidecar speaking JSON-RPC over stdio
- Supervise the brain sidecar and broker JSON-RPC
- Ping the brain from the renderer
- Introduce OKLCH design tokens
- Adopt shadcn/ui on Tailwind v4 with Geist typography
- Three-panel mosaic shell
- Theme switching across dark, light, and system
- Command palette skeleton (Cmd-K)
- LlmProvider trait + capability profile schema
- LlmProvider protocol + AnthropicProvider via Claude Agent SDK
- OS keychain adapter for API keys
- Provider settings panel + paste-API-key flow
- Chat session lifecycle with streamed JSON-RPC notifications
- Chat surface with streaming text and tool-call cards
- Provider selector in Settings
- LangGraph graph for the brain
- SqliteSaver checkpointer + per-run db files
- Runs index in app.db with list/get over JSON-RPC
- Inspector renders the live run
- Structured plan emission with rationale + cost
- Resume in-flight runs on app restart
- Interrupt_before=execute and resume contract
- JSON-RPC + Tauri command for plan approval
- Plan-approval modal with approve / edit / reject
- Per-run audit log writer
- Sub-agent lifecycle — spawn, observe, kill
- Expose run-tree and kill over the wire
- Sub-agent tiles in the inspector and chat surface
- Open a sub-agent into the main panel
- Take over a sub-agent into a fresh chat thread
- Depth cap with depth-gate approval for deeper spawning
- Sandbox trait + tier-0 bare-process implementation
- Tier-1 sandbox — devcontainer + git worktree
- Per-task egress allowlist for tier-1 sandboxes
- Restricted-shell tool with sub-agent allowlist
- Sandbox tier badge on tiles and detail view
- Per-run budget enforcement (tokens / time / iterations)
- Critic-agent invocation at budget checkpoints
- Heuristic drift score paired with the critic LLM
- Drift indicator + budget meter on agent tiles
- Review-drift CTA when the critic gate fires
- Natural-language → cron translator
- In-process schedule store + scheduler loop
- Sleep prevention during in-flight runs
- Durable resumption preserves the plan-approval gate
- Schedule dialog with natural-language and cron-expert modes
- OllamaProvider with tool-call normalization
- MLX provider for Apple Silicon local inference
- Capability profiles + delta surface
- Clickable provider switcher in the chat header
- Capability-delta dialog on provider swap
- Local-model availability check + Ollama pull
- Memory store + JSON-RPC bindings
- Auto-load THALYN.md / CLAUDE.md into chat context
- Memory dialog with read / edit / delete + add
- Structured memory-write surface — no silent writes
- Mount Monaco in the editor surface with themed integration
- LSP integration scaffolding for TypeScript and Python
- Ghost-text inline-suggest in the editor
- Xterm.js terminal pane backed by portable-pty
- Terminal-attach tool so agents can observe user shells
- Chromium sidecar lifecycle + supervisor
- Browser CDP attach + agent tool surface
- Browser surface + lifecycle commands
- Per-step DOM + screenshot capture for action-log replay
- OTel GenAI instrumentation for runs and LLM calls
- User-supplied Sentry DSN crash reporting
- Observability settings panel
- Tier 2 sandbox scaffolding (Firecracker + Lima)
- Tier 3 cloud sandbox scaffolding (E2B + Daytona)
- Tier escalation policy + user override
- MCP client and first-party connector catalog
- MCP connector marketplace and per-tool grants
- Gmail and Microsoft Graph adapters with hard-gated send
- Email surface and per-account credential management
- Wire Agents, Logs, and Connectors rail icons to real surfaces
- Promote connectors to a top-level surface
- Brain chat is permanent — split main into surface + chat
- Every surface gets an explicit close button
- Introduce yoyo migrations and capture v1 baseline schema
- Forward THALYN_DATA_DIR to brain on spawn
- Add v2 schema migration
- Add Python stores for v2 tables
- Fold v1 data into the v2 entity shape
- Register v2 IPC stubs returning NOT_IMPLEMENTED
- Add --inspect-db CLI for the three logical stores

### Performance

- Code-split editor, terminal, browser, email, sub-agent surfaces

### Refactoring

- Unify CEF profile path under THALYN_DATA_DIR

### Ci

- Bump Node heap on frontend + storybook + visual jobs
