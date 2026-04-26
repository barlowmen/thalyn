# ADR-0001 — Desktop runtime: Tauri 2

- **Status:** Accepted (provisional)
- **Date:** 2026-04-25
- **Supersedes:** —
- **Superseded by:** —

## Context

Thalyn is a from-scratch desktop app, not a fork of any existing IDE. It needs to host a code editor, embed a sidecar browser via CDP, supervise multiple long-running sidecar processes, deliver streaming agent output to the UI at sub-100 ms latency, and ship cross-platform (macOS, Linux, Windows) with friendly contributor onboarding. Built-in OS security model and small distribution footprint matter because the app is single-user-on-laptop and contributors should be able to clone and run quickly.

## Decision

Use **Tauri 2** (Rust core + web frontend) as the desktop runtime.

## Consequences

- **Positive.** Binary footprint roughly 25× smaller than Electron; cold-start ~4× faster. Memory baseline ~100 MB lower at idle. Rust core gives memory safety and zero-GC predictability for the IPC broker and sidecar supervisor. Tauri's permission/capability model maps cleanly onto our default-deny posture for sandboxes and connectors. The web frontend keeps the UI portable if we ever need to expose it elsewhere.
- **Negative.** Smaller plugin/community ecosystem than Electron; we may have to write or fork a handful of Tauri-side things that Electron has off-the-shelf. Windows IPC latency is the slowest path in the matrix and may need a coalescing buffer (see §12 of `02-architecture.md`). Mobile (iOS/Android) is alpha-tier; we explicitly don't target mobile in v1.
- **Neutral.** Rust is required of any contributor working on the core process, but the bulk of UI work stays in TypeScript/React.

## Alternatives considered

- **Electron** — same stack VS Code / Cursor / Windsurf use; largest ecosystem; rejected on binary size, memory baseline, and the optics of "yet another Electron app."
- **Native (SwiftUI for macOS, MAUI / Avalonia for cross-platform)** — best UX per platform; rejected because we need cross-platform parity from v1 and a single-codebase GUI (single-developer project for now).
- **Web-only PWA** — rejected because we need to spawn and supervise long-lived sidecar processes (sandboxes, brain, browser) and that's not the PWA contract.

## Notes

Re-evaluate at the v0.6 architecture-review checkpoint or sooner if Windows IPC latency turns out to be unworkable.
