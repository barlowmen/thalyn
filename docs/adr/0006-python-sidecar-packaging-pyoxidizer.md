# ADR-0006 — Python sidecar packaging: PyOxidizer (Briefcase fallback)

- **Status:** Superseded by [ADR-0018](0018-python-sidecar-packaging-pyinstaller.md)
- **Date:** 2026-04-25
- **Superseded:** 2026-04-26 (post-v0.6 architecture review)

## Context

The brain sidecar (ADR-0004) is Python and depends on the Claude Agent SDK, LangGraph, Mem0, the Ollama client, and miscellaneous OTel and MCP packages. Users should not have to install Python or manage a virtualenv. The sidecar must ship as a standalone binary on macOS, Linux, and Windows. Cold-start matters for app launch latency.

## Decision

Package the brain sidecar with **PyOxidizer**: produces a single binary that embeds a Python interpreter and all dependencies. The result is referenced as a sidecar binary in `tauri.conf.json` and bundled into Thalyn's installer. Fallback (only if PyOxidizer cannot handle our dep tree): **Briefcase + uv-managed venv** shipped alongside the app.

## Consequences

- **Positive.** Single artifact per OS — no installer-side Python detection, no system-Python interference, no "works on my machine because I have 3.12 but the user has 3.10." Faster cold-start than alternatives that boot a separate interpreter.
- **Negative.** PyOxidizer can be finicky with C-extension-heavy deps; the dep tree above is non-trivial. We must spike PyOxidizer packaging early in the project (`02-architecture.md` §12, risk #2) before committing further. Output binary is ~80 MB.
- **Neutral.** Build pipeline gains a Rust-toolchain dependency for sidecar packaging (we already have one for Tauri).

## Alternatives considered

- **PyInstaller** — older, simpler, larger binaries (~100 MB), slower start. Acceptable but PyOxidizer is the better default if it works.
- **Briefcase (BeeWare)** — produces native installers; ships a venv; viable as fallback.
- **uv-managed venv shipped alongside.** Simpler, but requires shipping the Python interpreter as files rather than embedded — bigger installer footprint.
- **Don't package — require user to install Python.** Rejected; conflicts with `01-requirements.md` F10.2 ("ship code in 10 minutes without reading docs").

## Notes

A PyOxidizer spike is the first technical task in v0.1.x. If the spike fails on macOS/Linux/Windows, we fall back to Briefcase before any dependent work begins.
