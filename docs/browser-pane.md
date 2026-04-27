# Browser pane — design reference

The Thalyn "browser" surface has two visible parts:

1. **The real Chromium window**, spawned and supervised by the Rust core. This is the user-facing browser. Logins, file uploads, downloads, IME, drag-drop, browser extensions, DRM video — all of it just works because Chromium is a real Chromium window with its own native chrome.
2. **The browser surface inside Thalyn**, a panel on the activity rail that shows status, lifecycle controls (Start / Stop), and — once an agent is driving — what the agent is up to. The panel is a CDP-driven observability + intervention console; it does **not** forward keyboard or mouse input to Chromium.

This doc captures the why and the boundary so future contributors don't accidentally re-litigate them.

## Why two surfaces

The 2026-04-26 spike (`docs/spikes/2026-04-26-webview-chromium-reparenting.md`) closed the question of "embed Chromium inside the WebView." Native window re-parenting is unviable cross-platform: Wayland blocks foreign-surface embedding outright, macOS cross-process subview embedding needs unstable Apple plumbing, and `wry#650` is closed not-planned. Routing input through CDP screencast as a primary surface looked workable until we walked the F4.3 flows — OAuth/2FA, file pickers, downloads, drag-drop, IME, DRM, extensions all collapse — and at that point we'd be rebuilding a browser shell in React and worse.

So the real Chromium window stays the user-facing browser. The Thalyn-side panel takes the parts CDP is great for: status, action overlay, screenshot preview, take-over controls.

## What the panel shows

| Surface | Source | Refresh cadence |
|---|---|---|
| Status badge (Idle / Starting / Running / Exited) | `browser_status` Tauri command | Polled every 2 s |
| Binary path, profile dir, DevTools WS endpoint | `browser_status` (Running variant) | Same |
| Per-step action log entries | Brain `run.action_log` notifications | Pushed on each action |
| Per-step DOM + PNG screenshot (links) | `runs/{id}/browser/<seq>.{html,png}` | Written by the brain after each tool call |
| Take-over button (raise the real Chromium window) | OS APIs via Tauri command | On user click |

The screencast preview frame stream and the take-over button are post-v0.13 work; the Tauri-side wiring lands once the OS window-raise helper (cocoa NSRunningApplication / Win32 SetForegroundWindow / wlr-foreign-toplevel) is in place.

## What the panel does not do

- **No keyboard or mouse forwarding to Chromium.** The user clicks in the real Chromium window directly. CDP `Input.dispatchMouseEvent` / `dispatchKeyEvent` are agent-only.
- **No tab strip, no address bar, no context menu, no DevTools button.** The real Chromium window already has all of these. Building Thalyn-side equivalents is duplicate work that drifts from upstream Chromium chrome.
- **No bundled Chromium.** v1 uses the user's installed Chrome / Chromium / Edge / Brave (discovered at start time). Bundling adds ~200 MB to the installer and would require us to track Chromium security releases.
- **No multi-session.** v1 supports one attached Chromium at a time. Multi-target (window.open, popups) ride a future refinement.

## How a session flows

1. User opens the Browser surface and clicks **Start**.
2. The Rust core's `BrowserManager` discovers a Chromium binary, spawns it with `--remote-debugging-port=0` and a per-Thalyn profile, polls the `DevToolsActivePort` file, parses the WS URL.
3. The core calls the brain's `browser.attach({wsUrl})`. The brain opens a CDP WebSocket, picks the active page target via `Target.getTargets`, attaches a session via `Target.attachToTarget` with `flatten=true`.
4. The panel renders the running state with the binary path, profile, and WS URL.
5. The brain enables per-step capture by calling `browser.set_capture_dir({runId, baseDir})`.
6. Agents call browser tools (`browser_navigate`, `browser_get_text`, `browser_click`, `browser_type`, `browser_screenshot`); each writes a DOM dump and PNG to `<baseDir>/<seq>.{html,png}` after a successful action.
7. The user can click directly in the real Chromium window any time — no special take-over mode required for the everyday flow.
8. Stop tears down brain detach first, then kills the Chromium child.

## Where the code lives

| Concern | Path |
|---|---|
| Chromium discovery (per-OS install paths) | `src-tauri/src/browser/discover.rs` |
| Spawn + DevToolsActivePort polling + lifecycle | `src-tauri/src/browser/supervisor.rs` |
| Single-session manager + Tauri commands | `src-tauri/src/browser/mod.rs`, `src-tauri/src/lib.rs` |
| Brain CDP transport | `brain/thalyn_brain/browser_cdp.py` |
| Brain BrowserManager + tool methods + capture | `brain/thalyn_brain/browser.py` |
| JSON-RPC bindings | `brain/thalyn_brain/browser_rpc.py` |
| Agent tool specs (mirror `terminal_tool`) | `brain/thalyn_brain/browser_tool.py` |
| Renderer surface | `src/components/browser/browser-surface.tsx` |
| Renderer Tauri wrappers | `src/lib/browser.ts` |

## Related docs

- [ADR-0010 — Browser: sidecar headed Chromium driven over CDP](adr/0010-browser-sidecar-chromium-cdp.md). The wider decision and its three v0.13 implementation refinements.
- [`docs/spikes/2026-04-26-webview-chromium-reparenting.md`](spikes/2026-04-26-webview-chromium-reparenting.md). The spike that retired the WebView re-parenting risk and reversed an earlier "screencast as primary" lean.
