# ADR-0019 — Browser engine: bundled Chromium via cef-rs, embedded in-process

- **Status:** Proposed
- **Date:** 2026-04-27
- **Supersedes:** ADR-0010

## Context

ADR-0010 chose a **sidecar headed Chromium driven over CDP** for the
in-app browser, with a follow-up refinement (`docs/spikes/2026-04-26-webview-chromium-reparenting.md`)
that the sidecar's *real Chromium window* is the user-facing surface and
the in-Tauri panel is a CDP-driven observability console. That decision
was sound under v1's framing.

Vision v2 (`04-vision-v2.md`) introduces a hard rule (`project_no_external_apps`):
**the user is never *forced* to leave the Thalyn app for any in-scope
workflow.** A Thalyn-driven flow that opens a separate Chromium OS
window is exactly the violation the rule was written to prohibit. The
prior spike retired the only fix considered there (cross-process
window re-parenting); the v2 rewrite needs a different engine
architecture.

The follow-up spike `docs/spikes/2026-04-27-browser-engine.md` evaluated
every credible 2026 option (system webviews via Wry; bundled CEF /
Ultralight / Sciter / Servo; hybrid Wry + headless Chromium; migration
to Electron; Chromium fork) against six criteria (hard-rule compliance,
agent CDP, cross-platform parity, bundle, maintenance, Tauri-2 fit) and
the F4.3 user-as-regular-browser flows. Two findings collapse the
option space:

- **OS-native webviews fail OAuth and fail uniform CDP.** Google's
  embedded-webview policy serves `disallowed_useragent` to WKWebView,
  WebView2, and WebKitGTK; among them only WebView2 exposes CDP. The
  daily-flow connectors in v2 scope (Slack, Gmail, Linear, Notion,
  GitHub) all front-end through Google or Microsoft OAuth.
- **Production 2026 apps that put a general-web in-app browser in
  front of users all ship full Chromium** — Atlas, Comet, Dia,
  BrowserOS as Chromium-fork browsers; Notion, Linear, Slack, Figma
  as Electron. The pattern is unambiguous and the cost (bundle,
  maintenance) is what every shipping comparable product has paid.

Of the bundled-Chromium paths, **`tauri-apps/cef-rs`** is the right
shape for our substrate: official Tauri-org Rust binding, dual MIT /
Apache-2.0 (compatible with ADR-0016), latest release `cef-v147.1.0+147.0.10`
on 2026-04-26 (Chromium 147), in-process embedding designed-in,
Chromium DevTools port for the agent CDP pipeline.

## Decision

Adopt **bundled Chromium via `tauri-apps/cef-rs`, embedded in-process
inside the Tauri main window** as the single in-app browser engine —
both the user-facing browser surface and the agent's CDP automation
target. ADR-0010's sidecar process supervisor retires; the brain's
`CdpConnection` and `browser_*` tools carry forward unchanged against
the in-process Chromium.

Concrete shape:

- **Engine.** `tauri-apps/cef-rs` pinned to a CEF-147 release; bumped
  per Chromium minor with the existing dependency-review cadence.
  CEF Chrome bootstrap + Alloy style (M125+ unified runtime).
- **Embedding.** CEF's Browser parented to a child `NSView` / `HWND`
  / `GtkWidget` of the Tauri main window. The browser drawer (per
  v2 §5.2) sizes and positions the parented native view; CEF paints
  directly into it. In-process, not cross-process.
- **User browsing.** Drawer-hosted CEF view with thin chrome (back /
  forward / URL / reload / "Open in system browser" escape). Cookies
  and login state persist in a per-Thalyn Chromium profile under the
  app data dir.
- **Agent automation.** Same `--remote-debugging-port=0` + `DevToolsActivePort`
  discovery; the brain attaches over WebSocket to the in-process
  CEF instance. The five `browser_*` tools (`navigate`, `get_text`,
  `click`, `type`, `screenshot`) and the per-step DOM + PNG capture
  to `runs/{run_id}/browser/<seq>.{html,png}` carry forward unchanged.
- **OAuth.** Default path is the in-app CEF surface with Chrome-style
  UA. Each OAuth step also exposes a single-click "Open in system
  browser" affordance — the user-driven escape from the hard rule's
  carve-out — so the rare IdP that refuses CEF still completes the
  flow without manual URL copying. The post-auth daily flow stays in
  the CEF surface.
- **Linux / Wayland.** v1 ships X11 / XWayland (CEF
  `ozone-platform=x11`). Native Wayland embedded-toplevel support is
  on the CEF roadmap (Toyota-sponsored, tracking issue #2804); when
  it lands we switch and add a Linux-Wayland row to the going-public
  checklist alongside it.

## Consequences

- **Positive.**
  - **Hard-rule compliant by default.** No second OS window exists;
    the user perceives a single app.
  - **Single engine for user + agent.** Eliminates the v1 sidecar
    process-supervisor surface (discovery, port-poll, kill-trigger,
    OS-window-raise on three platforms) — that whole codepath
    retires.
  - **Brain unchanged.** `CdpConnection` and the five `browser_*`
    tools speak the same CDP they spoke against the sidecar; the
    only difference is the WS URL points in-process.
  - **Full Chrome capability.** WebAuthn / passkeys, Widevine,
    file-picker via `OnRunFileChooser` with native dialogs, IME
    preedit, drag-drop, downloads, extensions, PDF — all the
    user-as-regular-browser flows that the prior spike found
    screencast-as-primary cannot deliver.
  - **License clean.** cef-rs MIT / Apache-2.0; CEF BSD; both
    compatible with ADR-0016. No GPL contagion.
- **Negative.**
  - **Installer +~130 MB compressed / +~250 MB on disk per platform.**
    Documented price of the hard rule; the same cost every Atlas /
    Comet / Notion / Slack pays. Targets first-run *time-to-first-
    conversation* (`project_first_run_flow`) are post-install and
    not threatened.
  - **Chromium maintenance burden.** ~6-week upstream cycles; cef-rs
    follows ~1 week behind. Folds into the existing
    `/dependency-review` cadence; needs a CEF/Chromium-stable CVE
    response SLO before public release (going-public-checklist row).
  - **Wayland native embedding not yet shipped.** v1 X11/XWayland
    path covers every shipping Wayland session at a small input-
    latency penalty. Tracked issue with industry sponsorship; not
    a research dead-end.
  - **OAuth heuristic detection on a small minority of IdPs.** The
    "Open in system browser" affordance is the documented
    user-driven escape; expected to be the rare exception, not the
    daily flow. Re-evaluate empirically if it trips for >10% of
    supported connectors.
- **Neutral.**
  - **CEF profile data-at-rest** (cookies, login state) is plaintext
    in the per-Thalyn profile dir for v1. Going-public bar is
    encryption-at-rest parity with the user's main browser; row on
    the checklist.
  - **The drawer layout from v2 §5.2 needs a "native-view host"
    primitive** the rest of the renderer doesn't share. Acceptable;
    it's exactly one drawer kind.

## Alternatives considered

- **Keep ADR-0010 (sidecar Chromium).** Rejected: violates the v2
  hard rule. Cross-process re-parenting is dead per the prior spike.
- **OS-native webviews via Wry (WebView2 / WKWebView / WebKitGTK).**
  Rejected. Google blocks all three under embedded-webview policy
  (`disallowed_useragent`); only WebView2 has CDP, so the agent
  pipeline can't be uniform across platforms; F4.3-equivalent flows
  (passkeys, DRM, IME, drag-drop) all degrade. The cleanest path on
  bundle and maintenance, but it loses on capability — the trade
  v2's positioning explicitly cannot make.
- **Hybrid: Wry user-browsing + headless Chromium for the agent.**
  Rejected. The OAuth case needs *some* Chromium the user can
  interact with; the hybrid leaves it unsolved. Two engines means
  twice the maintenance and twice the bundle (we'd ship Chromium
  *and* keep Wry) — strictly worse than CEF alone once the OAuth
  case is honest.
- **Migrate substrate to Electron** for `WebContentsView`. Rejected.
  Cost is multi-month substrate rework against five existing ADRs
  (0001, 0004, 0005, 0007, 0017). CEF-in-Tauri keeps the substrate
  and adds one new piece — the change is local to ADR-0010's scope.
- **Bundled non-Chromium engines.**
  - **Ultralight.** Proprietary core, $100K-cap indie license,
    closed source, missing video / WebRTC / WebGL maturity.
    Incompatible with ADR-0016 and with "the user uses it as a
    regular browser."
  - **Sciter.** Commercial UI engine, intentionally narrow CSS / JS
    subset; not a general-web browser. License starts at $310.
  - **Servo 0.1 (April 2026).** Embeddable, on stable Rust, but the
    project's own framing acknowledges general-web compat gaps; no
    DRM / passkey parity; companion Verso archived in 2025 because
    it couldn't keep pace. Two years premature for v2.
- **Fork Chromium.** Rejected: not a side-project's scope. This is
  what Atlas / Comet / Dia / BrowserOS chose; it's the right call
  if the *browser is the product*. For Thalyn the browser is one
  drawer kind among several.
- **CEF Off-Screen Rendering (OSR) as default instead of windowed.**
  Rejected. OSR reintroduces the screencast-as-primary failure modes
  the prior spike already documented (passkey UI, DRM, IME preedit).
  OSR remains an option for the Linux/Wayland fallback if needed.

## Notes

ADR-0010's status flips to **Superseded by ADR-0019** on accept of this
ADR. Its provisional refinements (v0.13 spike retirement; discovery and
lifecycle; brain CDP transport; renderer surface; per-step capture)
become historical context: the brain CDP transport survives unchanged,
the renderer surface is replaced by the drawer-hosted CEF view, the
sidecar process supervisor and OS-window-raise plumbing retire.

`02-architecture.md` §12 risk #1 stays retired by the prior spike; this
ADR adds a smaller risk (CEF native Wayland embedded-toplevel support
not yet shipped — v1 X11/XWayland path) to be tracked alongside.

The going-public-checklist gains rows for: CEF Chromium-CVE response
SLO; CEF native Wayland support; bundle-size review; Chromium-profile
encryption-at-rest.

The spike (`docs/spikes/2026-04-27-browser-engine.md`) carries the full
option-by-option rationale and the citations.
