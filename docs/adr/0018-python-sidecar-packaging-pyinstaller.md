# ADR-0018 — Python sidecar packaging: PyInstaller (uv-managed venv during early phases)

- **Status:** Accepted
- **Date:** 2026-04-26
- **Supersedes:** ADR-0006

## Context

The brain sidecar (ADR-0004) is Python and depends on the Claude Agent SDK, LangGraph, Mem0, an Ollama client, and miscellaneous OTel and MCP packages. Users should not have to install Python or manage a virtualenv. The sidecar must ship as a standalone artifact on macOS, Linux, and Windows. Cold-start matters for app launch latency.

ADR-0006 picked **PyOxidizer** as the primary path with **Briefcase** as a fallback. Two findings since then change the landscape:

1. **PyOxidizer is in maintenance limbo.** The maintainer hasn't meaningfully updated the project since January 2023; the project status thread is open for new maintainers but no one has stepped in (`docs/architecture-reviews/2026-04-26-v06.md` cites the upstream issue). Adopting an unmaintained packaging tool against a fast-moving Python dep tree (Claude Agent SDK ships releases monthly; LangGraph 1.x is on a steady cadence) is a slow-moving liability.
2. **The 2026 Tauri-with-Python-sidecar reference patterns all use PyInstaller.** Public worked examples — including the canonical `example-tauri-v2-python-server-sidecar` repo and several production write-ups — bundle the Python brain via PyInstaller and reference it as a Tauri sidecar binary. It is the path of least resistance for our exact stack.

We are not on the packaging phase yet — the brain is spawned via `uv run python -m thalyn_brain` during development — so we have not absorbed any PyOxidizer-specific cost. Switching the recommended path before packaging actually starts is the cheap moment.

## Decision

Package the brain sidecar with **PyInstaller**: produces a single executable (or one-folder bundle) that embeds a Python interpreter and all dependencies. The result is referenced as a sidecar binary in `tauri.conf.json` and bundled into Thalyn's installer.

During development and early phases we **continue to use a `uv`-managed venv** (`uv run python -m thalyn_brain`) — same as today. The PyInstaller path is exercised for the first time when the packaging phase opens, so the spike happens once, late, and deliberately.

**Fallbacks if PyInstaller hits a dep-tree issue:**

- **Briefcase + uv-managed venv** shipped alongside the Tauri binary (still the cross-platform-mobile path, though Thalyn doesn't target mobile).
- **Nuitka** — produces native binaries via Python-to-C compilation; viable but more invasive.

## Consequences

- **Positive.** Mature, actively maintained tool with broad community knowledge. Documented Tauri integration patterns. Single artifact per OS — no installer-side Python detection, no system-Python interference. PyInstaller spec files give us explicit control over hidden imports and binary deps, which we will need for the Claude Agent SDK + LangGraph + Mem0 dep tree.
- **Negative.** PyInstaller binaries are ~100 MB on a heavy dep tree (slightly bigger than PyOxidizer's expected ~80 MB). Cold-start is a hair slower than PyOxidizer's embedded-interpreter design — but PyOxidizer's marginal performance win is not worth the maintenance risk. Some users have reported that PyInstaller sidecars don't always close cleanly when the parent app exits; we add a parent-process watchdog in the brain's IPC loop to handle that case.
- **Neutral.** Build pipeline gains a PyInstaller step; we already have Python tooling on every contributor's machine via `uv`.

## Alternatives considered

- **PyOxidizer** (the previous decision). Rejected because of maintainer abandonment; see Context.
- **Briefcase only.** Better for cross-platform mobile (which Thalyn does not target). For desktop-only, PyInstaller is the more documented path.
- **Nuitka.** Compiles Python to C; impressive performance but more invasive against a dep tree this large.
- **Cosmopolitan Python.** Single-binary cross-OS support but limited to pure-Python tools as of 2026 — does not handle our C-extension-heavy deps.
- **Don't package — ship `uv` and have it pull the Python interpreter.** Rejected; conflicts with `01-requirements.md` F10.2 ("ship code in 10 minutes without reading docs"). Acceptable for development only.

## Notes

A PyInstaller spike is the first technical task of the packaging phase. If the spike fails on macOS / Linux / Windows for any reason that isn't a fixable spec-file issue, the fallback is Briefcase + uv-managed venv shipped alongside the app — same fallback ADR-0006 documented, just bumped one slot up the preference list.

The parent-process watchdog (brain exits when its parent Rust process disappears) lands in the same packaging phase regardless of whether we end up on PyInstaller or Briefcase, because it's the right behavior either way.

### Notes from the macOS spike

The PyInstaller path landed on macOS via `brain/thalyn-brain.spec`, `scripts/build-brain-sidecar.sh`, and a `bundle` dependency group on the brain (so PyInstaller only installs when packaging — `dev` and the default `uv sync` stay lean).

Three things worth carrying forward when the Linux + Windows paths follow:

- **Heavy deps need `collect_all`, not enumerated `hiddenimports`.** `claude-agent-sdk`, `langgraph`, `opentelemetry`, `sentry-sdk`, `yoyo`, `websockets`, `croniter`, and `httpx` all use dynamic imports / runtime discovery that the static analyzer misses. `PyInstaller.utils.hooks.collect_all` walks each package for hidden imports, data files, and bundled native binaries — far more reliable than maintaining a list by hand.
- **Yoyo migrations must ship as filesystem data.** The migration loader resolves `Path(__file__).parent.parent / "migrations"` and exec's `.sql` and `.py` files from disk. PyInstaller defaults to compiling `.py` into the archive, so the spec explicitly stages every file in `thalyn_brain/migrations/` as a data entry under `thalyn_brain/migrations/`.
- **One-folder bundle is ~260 MB on disk** with the current dep tree — about 2.5× the ~100 MB this ADR's "Consequences" section estimated. The langgraph + claude-agent-sdk + opentelemetry stack is heavier than the original guess assumed; logged on the going-public checklist for a release-cut review of bundle size.
