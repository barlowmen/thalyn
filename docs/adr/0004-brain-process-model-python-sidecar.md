# ADR-0004 — Brain process model: Python sidecar

- **Status:** Accepted (provisional)
- **Date:** 2026-04-25

## Context

The brain runs the Claude Agent SDK (Python). It also hosts LangGraph (Python) and Mem0 (Python). The Tauri main process is Rust. The brain has to be invocable from Rust, must stream output back, and must be killable and restartable independently of the main process. Embedding Python inside Rust via PyO3 is one option; running Python as a sidecar process is the other.

## Decision

Run the brain as a **separate Python sidecar process**, supervised by the Rust core, communicating over a local IPC socket. The sidecar is the only Python in the system.

## Consequences

- **Positive.** Crash isolation — a Python exception or memory blow-up takes down the brain only, and the supervisor restarts it; the Rust core, the WebView, and any in-flight UI state survive. Easier to debug — attach a Python debugger to the sidecar without involving Rust. Easier to ship — the Python interpreter stays self-contained (ADR-0006). No PyO3 build complexity; Rust contributors don't need a Python toolchain.
- **Negative.** IPC overhead vs. in-process calls — sub-10 ms on Unix sockets, acceptable for our streaming budgets.
- **Neutral.** The sidecar must be packaged for cross-platform distribution (handled in ADR-0006).

## Alternatives considered

- **PyO3 — embed Python in the Rust process.** Rejected: any Python crash takes down the whole app; build toolchain is more painful for contributors; debugging is awkward.
- **Replace Python with a non-Python brain** (Rust-native or TypeScript). Rejected because Claude Agent SDK is Python/TypeScript only and Python has the broader ecosystem (LangGraph, Mem0). The TypeScript SDK would let us do Node.js sidecar instead — potentially viable as a fallback if Python packaging proves too painful.

## Notes

If PyOxidizer (ADR-0006) doesn't pan out and the Python distribution story remains messy, the fallback is the TypeScript Claude Agent SDK + Node.js sidecar — same architecture, different runtime.
