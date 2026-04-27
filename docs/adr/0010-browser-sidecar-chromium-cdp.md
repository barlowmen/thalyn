# ADR-0010 — Browser: sidecar headed Chromium driven over CDP

- **Status:** Accepted (provisional)
- **Date:** 2026-04-25

## Context

Thalyn embeds a browser an agent can drive (`01-requirements.md` F4.3). Two approaches are common: (a) embed Chromium in-process via CEF, (b) spawn a headed Chromium as a sidecar process and drive it over the Chrome DevTools Protocol. Production tools (Devin, Manus) have settled on (b); CEF's maintenance burden is significant and version drift causes pain.

## Decision

Spawn **headed Chromium as a sidecar process** and drive it via CDP using either Stagehand v3 (TypeScript, called from the Rust core) or Browser-Use (Python, called from the brain sidecar) — both speak CDP, both ship as actively maintained OSS in 2026. Initial choice: Stagehand v3 (driven from the Rust core), with a Python adapter so brain agents can also issue commands.

## Consequences

- **Positive.** No CEF in our dep tree. Chromium versions independently from Thalyn — security patches don't require us to ship a release. CDP is a stable, well-tooled protocol; debugging an agent's browser session is the same as debugging a regular Chrome DevTools session. Cookies and sessions are isolated per Chromium profile (we ship a per-Thalyn profile).
- **Negative.** Embedding the headed Chromium *window* inside the WebView is non-trivial; cross-OS window-reparenting is shaky. Mitigation: ship as a separate window in v0.x, with an optional "embed as panel via screenshot+DOM mirror" mode if the spike succeeds (`02-architecture.md` §12 risk #1).
- **Neutral.** The user's main browser is untouched — Thalyn's Chromium is its own profile.

## Alternatives considered

- **CEF embedded.** Rejected; maintenance burden, large binary footprint, version-drift pain.
- **Use the system browser via CDP.** Rejected: collides with the user's tabs/cookies/profile.
- **WebView2 (Windows) / WKWebView (Mac).** Rejected: minimal CDP exposure; agents need full DevTools access.

## Notes

We document the "browser pane is a separate window in v0.x" decision visibly in user docs so it's not a surprise.

### Refinement after pre-v0.13 spike (2026-04-26)

The "embed as panel via screenshot+DOM mirror" line in **Consequences** was the only realistic alternative to native re-parenting; the [`webview-chromium-reparenting` spike](../spikes/2026-04-26-webview-chromium-reparenting.md) confirmed re-parenting is unviable cross-platform (Wayland blocks foreign-surface embedding outright; macOS cross-process subview embedding requires unstable Apple plumbing; `wry#650` is closed not-planned), and demolished screencast-as-primary by walking F4.3 flows (OAuth/2FA, file pickers, drag-drop, IME, downloads, DRM, extensions). The **Decision** above is unchanged — sidecar headed Chromium driven over CDP. What changes is the panel framing:

- The **real Chromium window is the user-facing browser surface.** It opens visibly when the sidecar starts and stays open for the user to interact with directly (logins, file uploads, downloads, IME, drag-drop, extensions, DRM video).
- The **in-Tauri panel is a CDP-driven observability + intervention console**, not an alternative input surface. It renders a low-cadence `Page.startScreencast` preview, an `Accessibility.getFullAXTree` snapshot per agent step, the action log, and "next planned action" highlights. **No keyboard or mouse forwarding from the panel to Chromium.**
- **Take-over** raises the real Chromium window via OS APIs (`NSWindow.makeKeyAndOrderFront` on macOS, `SetForegroundWindow` on Windows, `wlr-foreign-toplevel` activation where available on Wayland) and pauses the agent loop. The user uses Chromium directly.

`02-architecture.md` §12 risk #1 is retired by the spike; the risk register links here for the rationale.

### Refinement at v0.13 implementation — discovery + lifecycle

The first browser-sidecar commit ships the Rust-side process owner (no CDP client yet — that lands in the brain commit that follows). Three implementation choices are worth fixing in the ADR so the next maintainer doesn't relitigate them.

- **Discovery: use the user's installed browser, no bundling, no download.** The sidecar searches Chrome / Chromium / Edge / Brave at well-known per-OS paths (`/Applications/...` on macOS; `/usr/bin/...` and `/snap/bin/...` on Linux; `Program Files\...` on Windows). The `THALYN_BROWSER_BIN` env var overrides discovery for tests and power users. Bundling Chromium would add ~200 MB to the installer (a non-starter for an open-source desktop project) and downloading on first run breaks the offline-first promise from `01-requirements.md` NFR4. If no browser is found, the manager surfaces a clear error pointing the user at the install flow they prefer.
- **Profile: per-Thalyn `chromium-profile` under the app data dir.** Cookies and login state survive restarts; the user's main browser profile is untouched. The profile dir is created on first session start and never reset by Thalyn (the user can blow it away manually if they want a fresh state).
- **Lifecycle: spawn → poll DevToolsActivePort → expose WS URL.** Chromium picks its own port via `--remote-debugging-port=0` (no port-collision logic to maintain) and writes the chosen `(port, ws path)` to the file. We poll at 50 ms and cap the wait at 10 s; early child exit and timeout each surface as distinct typed errors. Termination shares one watcher task with exit detection — a `oneshot` kill-trigger races `child.wait()`, so termination cannot deadlock with natural death. **Auto-restart is deliberately not in the supervisor** — the right "should we respawn?" answer depends on whether an agent run is in flight, the user's last interaction, etc., so the manager exposes the death event for a higher layer to decide.

### Refinement at v0.13 implementation — brain CDP transport + tool surface

The original ADR named **Stagehand v3** as the agent-side CDP client of choice. Implementation walked back from that for two reasons:

- **Stagehand is TypeScript.** Adopting it would mean a Node sidecar in addition to the brain Python sidecar — a third process tree to supervise, a third packaging story (PyOxidizer doesn't help us), and a Node↔Python bridge for tool invocations. We already have one Python sidecar; we don't want another runtime.
- **browser-use** (the named alternative in the original ADR) is a Python agent framework on top of CDP. It ships a `BrowserSession.cdp_url=` attach mode that's the right shape, but the framework around it (its own LLM-driving Agent class, async hooks, observation pipeline) is weight we don't need — the brain already owns orchestration via LangGraph + Claude Agent SDK.

So we ship a **thin in-house CDP client** over `websockets` (~200 lines of Python) that exposes the half-dozen primitives the agent actually needs — `Page.navigate`, `Page.captureScreenshot`, `Runtime.evaluate`, `Input.dispatchMouseEvent`, `Input.insertText`, `Target.attachToTarget` — and call it a day. The full upstream-CDP surface stays available for future expansion via the same `CdpConnection.send` entry point.

The agent-tool surface mirrors the v0.12 terminal-tool shape: each verb gets its own structured spec (`browser_navigate`, `browser_get_text`, `browser_click`, `browser_type`, `browser_screenshot`) plus a Python entry. Five tools rather than one combined `browser_action` because the agent SDK plans clearer with one tool per verb and the action log reads better. SDK / MCP wiring lands once tool registration stabilises across providers (currently shared with `terminal_attach`).

The brain's `BrowserManager` is single-session — one attached Chromium at a time. Multi-session and multi-target navigation (`window.open` follow flows, popups, etc.) ride a future refinement; v1's scope is "the brain drives the page the user is on."

If we later need richer features (frame trees, accessibility-tree mirror for the panel overlay, network-domain auth flows) the path is layering them on top of `CdpConnection`, not migrating to Playwright/Stagehand/browser-use. We revisit that decision if the in-house surface starts duplicating non-trivial Playwright internals.
