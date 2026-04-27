---
date: 2026-04-26
risk: 02-architecture.md §12 risk #1 — WebView + headed Chromium re-parenting
adr: 0010
---

# Spike: webview-chromium-reparenting

- **Question:** Can we cleanly re-parent the headed Chromium sidecar's
  OS window into the Tauri WebView on macOS, Linux, and Windows, or do
  we need a different shape for the in-app browser pane?
- **Time-box:** 4 h nominal. **Actual:** ~1.5 h. Time-box was dropped
  partway through in favour of rigor; the answer landed before the
  original budget anyway.
- **Outcome:** Answered.

## Approach

1. Anchored on ADR-0010 (sidecar headed Chromium driven over CDP),
   which already calls re-parenting "shaky" and floats screenshot+DOM
   mirror as a fallback.
2. First research sweep against the 2026 state of: Tauri 2 native
   handle exposure, OS-specific foreign-window embedding (NSWindow /
   HWND / Wayland), and what shipping agentic-browser tools do today.
3. Second sweep to (a) verify the first sweep's citations — several
   were wrong or weak — and (b) stress-test the first sweep's
   "screencast as primary surface" recommendation against requirement
   F4.3 ("the user can also use it as a regular browser"): file
   pickers, OAuth/2FA, IME, drag-drop, DRM, extensions, audio,
   downloads.
4. No prototype. The viability question is decisive enough from
   protocol docs and shipping prior art that empirical numbers
   wouldn't change the call. Latency / fps numbers will land as a
   v0.x perf check once the panel is real.

## Findings

### F1. Native window re-parenting is not a viable cross-platform path

- **Wayland.** `xdg-foreign-unstable-v2` only exposes stacking and
  parenting semantics — it cannot embed a foreign toplevel as a child
  surface. There is no `xdg-toplevel-embed` proposal in
  `wayland-protocols` 1.48 (released April 2026) or in the staging
  tree. wlroots compositors expose `wlr-foreign-toplevel-management-v1`
  for activation/raise but not embedding; Mutter does not implement
  it at all.
- **macOS.** `Window::ns_window()` exposes the NSWindow and
  `with_webview()` exposes the underlying WKWebView's NSView.
  Cross-process `addChildWindow:ordered:` works for stacking;
  cross-process `addSubview:` requires private XPC / `NSRemoteView`
  plumbing that Apple has never made stable.
- **Windows.** Cross-process `SetParent(child, parent)` is the most
  workable platform. Focus, IME, and `WS_EX_NOREDIRECTIONBITMAP`
  quirks are documented but solvable.
- **Tauri side.** [wry#650](https://github.com/tauri-apps/wry/issues/650)
  ("Construct a `WebView` from a raw window handle") was closed
  not-planned by maintainers. There is no MIT/Apache cross-platform
  Rust crate that abstracts foreign-window parenting; the closest is
  [`aloe-embedding`](https://crates.io/crates/aloe-embedding), which
  is GPL-3.0 and incompatible with our license (ADR-0016).

Even if we shipped re-parenting on macOS + Windows, Linux/Wayland
users would get a broken or absent browser pane — which fails NFR5
(cross-platform parity) outright.

### F2. CDP screencast as the *primary* user input surface fails F4.3

The first research sweep's recommendation — "render screencast in a
React panel and route input back via `Input.dispatchMouseEvent` /
`dispatchKeyEvent`" — collapses once you walk the F4.3 flows. Each
row below is a real user flow; the cost column is what
screencast-as-primary actually demands:

| User flow | Cost under screencast-as-primary |
|---|---|
| Google login / OAuth popup / WebAuthn / passkey | New CDP target per popup; OS keychain / passkey / hardware-key flows can't reach a screencasted surface. |
| `<input type="file">` | `Input.dispatchFileChooser` does not show the OS picker — we'd glue Tauri's `dialog::open` to a path injection. |
| Download | Lands wherever Chromium's `downloadPath` is set (sandbox dir by default); needs `Browser.setDownloadBehavior` plumbing to surface in `~/Downloads`. |
| Drag-drop from desktop into a web app | Broken. Tauri receives the drop, not the screencasted Chromium. |
| IME (CJK composition) | CDP `Input.insertText` doesn't model preedit composition cleanly; underline rendering won't match. |
| Extensions (uBlock, 1Password, Bitwarden) | In-page effects show in screencast, but extension popups (browser-action UI) are out-of-document and unreachable. |
| Audio (YouTube while working) | Plays from headed Chromium directly — leaks regardless of Tauri window focus, or muted entirely. |
| DRM video (Netflix, Disney+) | Black under capture; Widevine refuses to render to a screencast surface. |
| Right-click / Inspect / Save Image As | Whole context menu rebuilt in React; "Inspect" specifically requires a separate DevTools target, defeating the point. |
| `window.open` / multi-window | Each new target needs its own screencast session and tab UI. |
| Back/forward/address bar | Custom React chrome calling `Page.navigate` etc. |

This quietly turns into "rebuild a browser shell in React, but
worse." F4.3 is satisfied only by the *real* Chromium window.

### F3. CDP *is* the right protocol — for the agent and for observation

Stagehand v3 went CDP-native in late 2025 and exposes a structured
a11y/DOM tree to agents
([Browserbase blog](https://www.browserbase.com/blog/stagehand-v3)).
browser-use moved off Playwright onto raw CDP for the same reason
([browser-use post](https://browser-use.com/posts/playwright-to-cdp)).
Manus's reverse-engineered architecture is also CDP-driven via a
local extension acting as an MCP server
([Mindgard write-up](https://mindgard.ai/blog/manus-rubra-full-browser-remote-control)).

CDP is the right protocol for *the agent driving the browser* and
for *us observing what it's doing*. It is the wrong protocol for
*the user using the browser*.

## Recommendation

**Adopt approach B: real Chromium window stays the user-facing
interactive surface; the in-Tauri panel is an observability +
intervention console.** This is the lean ADR-0010 already had; the
spike confirms it and drops the "fallback" framing.

ADR-0010 is **refined, not superseded**: the "Mitigation: ship as a
separate window in v0.x, with an optional embed-as-panel via
screenshot+DOM mirror if the spike succeeds" sentence becomes "the
panel is a CDP-driven observability mirror, not an alternative input
surface; the real Chromium window is the user-facing browser." The
status stays Accepted.

### What v0.13 should actually build

- **Sidecar lifecycle.** Spawn headed Chromium with
  `--remote-debugging-port=0 --user-data-dir=<thalyn-profile>`; parse
  the chosen port from `DevToolsActivePort`; open a CDP WebSocket.
  Window visible from spawn (not headless, not offscreen). Persist
  the profile under the user data directory so cookies and login
  state survive restarts. Supervisor restarts on crash within the
  existing 5 s budget. NSWindow / HWND tracking is only enough to
  raise/focus the real window on user request — no re-parenting.
- **Observability panel inside Tauri.** React pane sources:
  `Page.startScreencast` at `everyNthFrame: 4` (low-cadence preview,
  not an input surface), `Accessibility.getFullAXTree` snapshots per
  agent step, and the action log streamed from the brain. Renders:
  live thumbnail, current URL, last N agent actions, "next planned
  action" highlighted on the most recent screenshot via a11y bounds.
  **No keyboard or mouse forwarding from the panel to Chromium.**
- **Intervention handoff.** Pause / resume / stop in the panel; "take
  over" raises the real Chromium window via
  `NSWindow.makeKeyAndOrderFront` (macOS) / `SetForegroundWindow`
  (Windows) / wlr-foreign-toplevel activation where available
  (Wayland) and pauses the agent. Agent state persists across
  handoff. No input re-routing — the user uses Chromium directly.
  Resume picks up against whatever URL they navigated to.
- **DOM/screenshot capture for action-log replay.** Per-step DOM
  snapshot + screenshot stored under `runs/{id}/browser/`.
  Independent of the live panel; this is what fills the action log
  for after-the-fact replay.

### What we are *not* building in v0.13

- Approach **A** — screencast-as-primary. Fails F4.3 across the board.
- Approach **C** — hybrid pop-in/out via screencast input. Doubles
  input-routing surface for marginal value. Defer until user
  research demands it.
- A custom React browser chrome (address bar, tabs, history). The
  real Chromium window already has these.

### Plan adjustments

- ADR-0010 is refined per the above. The "browser-pane.md —
  embed-vs-separate decision" doc planned for v0.13 reduces from a
  decision artifact to a one-page "real window primary, panel is
  observability" reference.
- The architecture risk in `02-architecture.md` §12 risk #1 (WebView
  + headed Chromium re-parenting) is **retired** by this spike. The
  risk register should mark it resolved with a link here.
- An open question for v0.13 remains: Stagehand v3 vs Browser-Use as
  the agent-side library. Both are CDP-native and viable; pick on
  ergonomics during v0.13 commit work and ADR.

## Risks not retired

- **Live screencast performance bar in our process tree.** Prior art
  (Manus, browser-use desktop, vercel-labs/agent-browser) shows
  low-cadence CDP screencast is fine for an observability preview,
  but we have no first-hand numbers from this codebase. Worth a
  small perf measurement once the panel exists; not a spike-blocker.
- **Window raise / focus on Wayland.**
  `wlr-foreign-toplevel-management-v1` exists on wlroots compositors
  but is not part of `wayland-protocols`, and GNOME Mutter does not
  implement it. The "take over" flow may need a degraded mode on
  Wayland (panel says "switch to the Thalyn Chromium window
  manually"). Worth a follow-up ADR before the take-over commit
  lands.
- **Tier-2 sandbox + Chromium.** When the microVM sandbox upgrade
  arrives, headed Chromium inside a microVM is its own problem
  (host audio routing, GPU acceleration, DRM). Independent of the
  embedding question; lives with the sandbox-tier work, not here.

## Sources

- [Tauri Window docs (`ns_window()`, `hwnd()`, `with_webview()`)](https://docs.rs/tauri/latest/tauri/window/struct.Window.html)
- [tauri-apps/wry#650 — WebView from raw window handle, closed not-planned](https://github.com/tauri-apps/wry/issues/650)
- [crates.io — `aloe-embedding` (GPL-3.0)](https://crates.io/crates/aloe-embedding)
- [xdg-foreign-unstable-v2 protocol](https://wayland.app/protocols/xdg-foreign-unstable-v2)
- [wayland-protocols 1.48 release announcement](https://www.mail-archive.com/wayland-devel@lists.freedesktop.org/msg44067.html)
- [Stagehand v3 — CDP-native agent stack](https://www.browserbase.com/blog/stagehand-v3)
- [browser-use: moving from Playwright to CDP](https://browser-use.com/posts/playwright-to-cdp)
- [Mindgard — Manus Rubra full browser remote control](https://mindgard.ai/blog/manus-rubra-full-browser-remote-control)
- [Chromium auto-throttled screen capture and mirroring](https://www.chromium.org/developers/design-documents/auto-throttled-screen-capture-and-mirroring/)
- [chromedevtools/devtools-protocol#63 — screencast fps caveat](https://github.com/ChromeDevTools/devtools-protocol/issues/63)
- [vercel-labs/agent-browser — reference implementation](https://github.com/vercel-labs/agent-browser)
- [No Hacks — Agentic Browser Landscape 2026](https://nohacks.co/blog/agentic-browser-landscape-2026)
