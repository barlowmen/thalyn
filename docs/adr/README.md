# Architecture Decision Records

This directory contains Thalyn's ADRs — one decision per file, in [MADR](https://adr.github.io/madr/) format.

## Conventions

- Filenames: `NNNN-short-slug.md` (zero-padded 4-digit number, kebab-case slug).
- Status: `Proposed`, `Accepted`, `Accepted (provisional)`, `Deprecated`, `Superseded by ADR-NNNN`.
- ADRs are **immutable once accepted**. To revise a decision, write a new ADR that explicitly **supersedes** the old one and update the old one's status.
- Keep each ADR short — context, decision, consequences, alternatives. The architecture overview lives in `ARCHITECTURE.md` (later) and `02-architecture.md` (current draft).

## Index

| # | Title | Status |
|---|---|---|
| [0001](0001-desktop-runtime-tauri-2.md) | Desktop runtime: Tauri 2 | Accepted (provisional) |
| [0002](0002-frontend-stack-react-shadcn.md) | Frontend stack: React 18 + shadcn/ui + Tailwind + Vite | Accepted (provisional) |
| [0003](0003-code-editor-monaco.md) | Code editor: Monaco | Accepted (provisional) |
| [0004](0004-brain-process-model-python-sidecar.md) | Brain process model: Python sidecar | Accepted (provisional) |
| [0005](0005-rust-python-ipc-ndjson-jsonrpc.md) | Rust ↔ Python IPC: NDJSON + JSON-RPC 2.0 | Accepted (provisional) |
| [0006](0006-python-sidecar-packaging-pyoxidizer.md) | Python sidecar packaging: PyOxidizer | Superseded by ADR-0018 |
| [0007](0007-orchestration-langgraph-claude-sdk.md) | Orchestration: LangGraph 1.0 + Claude Agent SDK | Accepted (provisional) |
| [0008](0008-durable-execution-sqlitesaver.md) | Durable execution: LangGraph SqliteSaver | Accepted (provisional) |
| [0009](0009-memory-mem0-langgraph-checkpoints.md) | Memory: Mem0 + LangGraph checkpoints + project files | Accepted |
| [0010](0010-browser-sidecar-chromium-cdp.md) | Browser: sidecar Chromium driven over CDP | Accepted (provisional) |
| [0011](0011-sandbox-tiers-devcontainer-microvm.md) | Sandbox tiers: devcontainer + worktree default; microVM opt-in | Accepted (provisional) |
| [0012](0012-provider-abstraction.md) | Provider abstraction: in-process trait + adapters | Accepted (provisional) |
| [0013](0013-design-system-oklch-geist.md) | Design system: OKLCH tokens, Geist typography, three-panel mosaic | Accepted (provisional) — layout claim refined by ADR-0026 |
| [0014](0014-documentation-madr-mermaid.md) | Documentation: MADR + Mermaid C4 + ARCHITECTURE.md | Accepted (provisional) |
| [0015](0015-commit-hygiene-conventional-commits.md) | Commit hygiene: Conventional Commits + git-cliff + leakage scan + no Co-Authored-By | Accepted (provisional) |
| [0016](0016-license-mit.md) | License: MIT (revisit before public) | Superseded by ADR-0030 |
| [0017](0017-observability-otel-langfuse.md) | Observability: OpenTelemetry GenAI + self-hosted Langfuse | Accepted (provisional) |
| [0018](0018-python-sidecar-packaging-pyinstaller.md) | Python sidecar packaging: PyInstaller (uv-managed venv during early phases) | Proposed |
| [0020](0020-brain-auth-backend-split.md) | Brain auth-backend split: Claude subscription default, API-key secondary | Proposed |
| [0024](0024-project-mobility-and-pluggable-classifier.md) | Project mobility (merge) + pluggable classifier interface | Accepted |
| [0025](0025-voice-input-stt.md) | Voice input: Whisper.cpp local-first STT with Deepgram cloud fallback | Accepted |
| [0026](0026-chat-first-shell-and-drawer-system.md) | App shape: chat-first shell + on-demand drawer system | Accepted |
| [0027](0027-info-flow-drift-critic.md) | Information-flow drift: critic generalization across the EM hierarchy | Accepted |
| [0029](0029-in-process-cef-tao-integration.md) | In-process CEF embedding: tao integration via runtime swizzle | Accepted |
| [0030](0030-license-apache-2.md) | License: Apache-2.0 (supersedes ADR-0016) | Accepted |
| [0031](0031-repo-public-source-visibility.md) | Repo public: source visibility precedes app distribution | Accepted |
