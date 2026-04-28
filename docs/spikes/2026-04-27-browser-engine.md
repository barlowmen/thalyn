---
date: 2026-04-27
risk: 04-vision-v2.md §0 hard rule (user never leaves the app) vs ADR-0010 (sidecar Chromium spawns its own window)
adr: 0010 → 0019 (proposed supersede)
---

# Spike: browser-engine (v2)

- **Question:** Under v2's *user never leaves the app* hard rule, what is the
  right in-app browser-engine architecture? The v1 sidecar pattern (ADR-0010)
  spawns its own OS window and so violates the rule on its default flow; the
  prior spike (`2026-04-26-webview-chromium-reparenting.md`) retired the only
  considered fix (cross-process re-parenting). The option space is wider than
  was evaluated then, and the rewrite needs the answer locked before
  architecture is rebaselined.
- **Time-box:** 4 h nominal. **Actual:** ~2 h. The decisive constraints
  collapsed the option space cleanly enough that benchmarks were not needed
  to call it.
- **Outcome:** Answered. Proposed: bundled Chromium via `tauri-apps/cef-rs`,
  embedded in-process inside the Tauri main window. Drafted ADR-0019
  supersedes ADR-0010.

## Approach

1. Anchored on the v2 hard rule (`project_no_external_apps` memory + v2
   vision §0) and the retired prior spike. The rule's wording is precise:
   *Thalyn-driven* flows must default to in-app; an "Open in system browser"
   exists only as a user-driven escape hatch. That distinction shapes the
   evaluation criteria.
2. Listed every credible 2026 option, including the three the rule's memo
   names (Wry-multi-window, CEF/bundled, hybrid) and several it does not
   (Servo 0.1, Ultralight, Sciter, Electron migration, Chromium fork).
3. Scored each against six criteria (hard-rule compliance, agent CDP, cross-
   platform parity, bundle, maintenance burden, Tauri-2 fit) and against the
   F4.3 user-as-regular-browser flows the prior spike already enumerated
   (OAuth/2FA, file pickers, drag-drop, IME, downloads, DRM, extensions).
4. Walked the OAuth case explicitly. It is the single hardest scenario under
   the hard rule because Google (and every major IdP that follows the same
   policy) blocks embedded webviews via `disallowed_useragent`, and OAuth is
   on the daily critical path for the "Thalyn, set up Slack/Gmail/Linear for
   me" flows already in scope (`project_conversational_onboarding`).
5. Surveyed how 2026 desktop apps actually solve this in production: Atlas,
   Comet, Dia, BrowserOS (full-Chromium browser shells); Notion, Linear,
   Figma, Slack (Electron, full Chromium); Raycast (native, no embedded web);
   the prior art the v1 ADR cited (Stagehand v3, browser-use, Manus). The
   pattern in 2026 is unambiguous: every app that needs OAuth-capable
   in-app browsing ships full Chromium.
6. No prototype. cef-rs released `cef-v147.1.0+147.0.10` on 2026-04-26 (the
   day before this spike) and ships `cefsimple` as a working in-process
   embedding example; the embedding viability question is decisive from the
   protocol + binding shape and does not need a perf number to call. Perf
   measurement lands when the panel is real.

## Findings

### F1. The hard rule kills the v1 default flow

ADR-0010 spawns a headed Chromium *with its own visible OS window*. Under
v2's `project_no_external_apps` rule, that is exactly the violation
("sidecar Chromium that spawns its own window") and is named in the memo
as the prompt for this spike. The prior spike already proved that
re-parenting that window into the Tauri main window is dead cross-platform
(Wayland prohibits foreign-toplevel embedding; macOS cross-process
`addSubview:` requires unstable Apple plumbing; `wry#650` closed
not-planned; the only abstracting Rust crate is GPL-3.0). So the v1
default flow does not survive, and we cannot reach a hard-rule-compliant
v1 by *fixing* it — we have to pick a different engine architecture.

The escape-hatch wording matters: "Open in system browser" is allowed,
but only when the *user* asks for it. The default agent-and-user browsing
loop must stay in-app.

### F2. OS-native WebViews (Wry / WKWebView / WebView2 / WebKitGTK) fail OAuth and fail agent CDP — both load-bearing

The cleanest-looking option fails on two of the six criteria.

- **OAuth.** Google's policy explicitly lists `WKWebView` (macOS), the
  Edge Chromium engine inside `WebView2` (Windows), and Android `WebView`
  / WebKitGTK as embedded webviews and serves `disallowed_useragent` to
  them. Apple's [WebKit blog post](https://webkit.org/blog/13936/enabling-the-inspection-of-web-content-in-apps/)
  on inspectable WKWebView reinforces the embedded framing — the engine
  identifies as embedded and major IdPs reject it. This is not theoretical;
  the auth0/[Disallowed_useragent](https://community.auth0.com/t/403-disallowed-useragent-for-web-login-from-embedded-browsers/55074)
  threads catalogue the failures. Setup flows for Gmail / Slack / Linear /
  Notion / GitHub all front-end through Google or Microsoft OAuth and
  every one of those breaks in a Wry surface.
- **Agent CDP.** Of the three system webviews, only WebView2 exposes the
  Chrome DevTools Protocol natively
  ([tauri-cdp](https://github.com/Haprog/tauri-cdp); the
  [Playwright docs](https://playwright.dev/docs/browsers) say standard
  Playwright connection is "impossible on macOS and Linux" because
  WKWebView and WebKitGTK have no CDP). The
  [`tauri-plugin-playwright`](https://lib.rs/crates/tauri-plugin-playwright)
  workaround embeds a control server *inside the app* — useful for
  Tauri-app E2E testing, but the agent's in-app browser would have to
  cooperate with our control server rather than expose a stable
  upstream-equivalent CDP surface. WKWebView has Web Inspector remote
  protocol with `com.apple.security.get-task-allow`, and WebKitGTK has
  WebDriver, but neither is CDP-equivalent. The brain's existing
  `CdpConnection` Python client (~200 lines, ADR-0010 refinement)
  cannot drive these surfaces.
- **F4.3 / drag-drop / passkeys / DRM.** WKWebView and WebKitGTK both
  carry the same embedded-context restrictions the prior spike
  enumerated for screencast-as-primary: no Widevine in WebKitGTK, no
  passkey UI in WKWebView (gated to first-party Safari).

The "OS WebView in the drawer" path is the most appealing on bundle,
maintenance, and Tauri-fit. It fails on the two criteria that are
non-negotiable for v2.

### F3. Bundled non-Chromium engines do not clear the bar

- **Ultralight.** Proprietary engine, free only under a $100K-revenue
  indie cap and only with WebKit attribution; the core (`UltralightCore`,
  `Ultralight`) is closed-source — incompatible with our MIT license
  (ADR-0016). HTML5 video / WebRTC / WebGL marked experimental. Fails
  license, fails feature-completeness for "the user uses it as a regular
  browser."
- **Sciter.** Commercial UI engine (license starts at $310 lifetime). It
  is not a general-web browser engine — its CSS / JS subset is
  intentionally narrow and most modern web apps will not run. Fails the
  *user uses it as a regular browser* requirement outright.
- **Servo 0.1.** Released April 2026 with a stable embedding API on
  stable Rust ([servo.org](https://servo.org/)). Genuinely embeddable.
  But the project's own framing is candid: *"Servo won't replace
  Chromium for general web browsing. Web compatibility gaps are real."*
  No DRM, no Widevine, no passkey/WebAuthn parity, no Chrome extension
  surface. The companion Verso browser project (which would have surfaced
  these gaps in production) was archived in 2025 because it could not
  keep pace with Servo revisions. Two years premature for the v2 use
  case; revisit at v2 + 1y.

### F4. Bundled Chromium via CEF, in-process: the only option that passes every criterion

`tauri-apps/cef-rs` is the official Tauri-org Rust binding for CEF. The
shape relevant to v2:

- **License.** Dual MIT / Apache-2.0 — compatible with ADR-0016. CEF
  itself is BSD. No GPL contagion (the failure mode of `aloe-embedding`
  in the prior spike).
- **Maintenance.** Latest release `cef-v147.1.0+147.0.10` on 2026-04-26
  (the day before this spike), tracking Chromium 147. 284 releases over
  the project's life with the cadence holding ~1 week behind upstream
  CEF. The Tauri org maintains it; this is not a single-author crate.
- **In-process embedding.** CEF's Browser host is created with a
  `CefWindowInfo` that takes a parent `HWND` / `NSView` / `GtkWidget`.
  In-process embedding is the use case CEF was *designed for*. Cross-
  platform parity is built in at the engine level — not at our layer,
  not at Tauri's layer. The Wayland edge is the one open question;
  see F5.
- **Engine surface.** Full Chromium 147: WebAuthn, passkeys, Widevine,
  IME (full preedit composition), file pickers via `OnRunFileChooser`
  with native OS dialogs, drag-drop in-page, downloads, extensions,
  PDF viewer. No reconstruction of any browser-shell concern.
- **CDP for the agent.** CEF exposes Chromium's `--remote-debugging-port`
  flag unchanged. The brain's existing `CdpConnection` Python client
  (~200 lines) carries forward as-is — the `WebSocket` URL just points
  at our in-process Chromium instead of a sidecar Chromium. The CDP
  surface (`Page.navigate`, `Input.dispatchMouseEvent`, etc.) is
  identical. This is a meaningful "no rewrites in the brain" win.
- **Runtime mode.** M125+ unifies Chrome bootstrap with Alloy style;
  the right pairing for v2 is Chrome bootstrap + Alloy style — this
  gives full Chrome features (Google Identity-friendly UA, the full
  Chromium API surface) while exposing the client-callback hooks
  (`OnRunFileChooser`, `OnBeforeBrowse`, parent-window control) we need.
- **Industry signal.** Atlas, Comet, Dia, and BrowserOS are all
  full-Chromium browser shells in 2026; Notion, Linear, Slack, Figma,
  1Password 8 are all Electron (full Chromium); Raycast deliberately
  avoids embedded web entirely. *Every* app in 2026 that puts a
  general-web in-app browser in front of users ships full Chromium.
  Wry-only paths win on bundle and lose on capability — and that
  trade is incompatible with v2's positioning.

### F5. CEF on Wayland: ship X11/XWayland for v1, revisit native Wayland in a follow-up

[Phoronix tracking](https://www.phoronix.com/news/Chromium-CEF-Wayland-Progress)
and [chromiumembedded/cef#2804](https://github.com/chromiumembedded/cef/issues/2804)
show CEF is *progressing* on native Wayland but not landed for embedded
toplevels: the X11 code in CEF is not yet replaced with Wayland-native
plumbing for the in-third-party-app embedding case. Toyota is
sponsoring the work; it is not a research dead-end like foreign-toplevel
embedding was. Two ship paths today:

1. **X11 / XWayland.** CEF builds with `ozone-platform=x11`; runs under
   XWayland on every modern Wayland session (GNOME / KDE / wlroots /
   Sway). Functional parity with the X11 path; minor input-latency
   penalty under XWayland.
2. **Off-Screen Rendering (OSR).** CEF renders to a buffer; we composite
   into the Tauri drawer; input forwarded via `SendMouseClickEvent` /
   `SendKeyEvent`. No OS window at all. Reintroduces the prior spike's
   "screencast-as-primary" failure modes for OAuth/passkeys/DRM/IME —
   so OSR is a degraded fallback, not a default.

Recommendation: ship X11/XWayland on Linux for v1; track CEF native
Wayland landing and switch when it does. Document the X11 dependency in
the Linux install instructions. The going-public-checklist already gates
v1 → public; native Wayland support gets a row there.

### F6. Why the *hybrid* (Wry user + headless Chromium agent) loses

The `project_no_external_apps` memo names this option. Walking the
flows:

| Flow | Hybrid behavior |
|---|---|
| User reading a doc on a website | Wry shows it — works (until Google content blocks embedded WKWebView, which Google Docs *does*) |
| User-driven OAuth login (Slack / Gmail) | Wry: blocked. Headless Chromium: blocked (no UI). System-browser handoff: violates *Thalyn never chooses the external path*. |
| Agent navigates and acts | Headless Chromium via CDP — works |
| User intervenes mid-agent task | Wry can't show what headless Chromium sees; observability preview only via screencast — same as the v1 panel |
| Drag-drop a file from desktop into a SaaS | Wry receives drop, target site is in headless Chromium — hybrid breaks; Wry-only would work but only for sites that don't trip embedded-webview blocks |
| Watch a tutorial video on YouTube | Wry: works on macOS/WKWebView; broken on WebKitGTK (no Widevine); WebView2 mixed |

Two engines means two engines to keep current, two surfaces to debug,
two install footprints, and the OAuth case still needs *some* Chromium
the user can interact with. By the time we have made the hybrid
hard-rule-compliant, we have brought a full Chromium into the bundle
*and* kept the Wry path — strictly worse than just CEF.

### F7. Why we don't migrate to Electron

Electron's `WebContentsView` is the canonical pattern for embedding a
sub-browser inside a desktop app — and it's how Notion, Linear, Slack,
Figma do it. If we were starting from zero, Electron would be a
defensible answer.

We are not starting from zero. Tauri 2 + Rust core + Python brain
sidecar + LangGraph + the IPC and observability stack are five ADRs
deep (0001, 0004, 0005, 0007, 0017). Electron means a Node main process,
which means re-architecting the Rust core's role (or accepting a
Node↔Rust bridge for the existing Rust IPC code), repackaging the
Python sidecar's spawn/lifecycle from Tauri's tooling to Electron's,
and rewriting the test/build harness. The cost is measured in months
of substrate work, not in features. CEF-in-Tauri lets us keep the
substrate intact and add one new piece (the CEF binding) — the change
is local to ADR-0010's scope.

### F8. The OAuth-block question, decisively

Even with full Chromium via CEF, Google's policy may flag the engine
under heuristic detection (CEF reports a CEF-derived UA by default,
and beyond UA, missing Google API keys are a fingerprint). Two layers
of defense:

1. **Default path.** CEF chrome-bootstrap with Chrome-style UA spoofing
   is sufficient for the vast majority of OAuth flows (the only thing
   *all* IdPs check is UA). Slack, Linear, Notion, Microsoft 365,
   Auth0-fronted apps work uneventfully. Google specifically may or
   may not trip — but Google also publishes
   [RFC 8252 native-app guidance](https://developers.google.com/identity/protocols/oauth2/native-app):
   loopback redirect URI + PKCE + system browser is the
   *recommended* path for native apps. We can do exactly that *with
   CEF as the system browser*, since to Google's checks CEF-with-
   Chrome-style is closer to Chrome than any embedded webview is.
2. **Escape hatch on detection failure.** If a specific IdP refuses
   the in-app surface (we will see this in QA, and rarely in
   production), we surface a single-click "Open in system browser"
   button on that step. The user clicks; system browser opens; the
   user logs in; provider redirects to our loopback HTTP server;
   token captured; flow returns to in-app. This is *user-driven*
   (they clicked the button), so the hard rule's user-driven escape
   carve-out applies. It is also infrequent — OAuth is a per-connector
   one-time setup, not a daily flow.

This is a clean position: in-app by default, user-driven escape
available, and the daily flow (interacting with the connected
service through CEF) never trips the policy at all because the
session cookie has already been issued.

## Recommendation

**Adopt bundled Chromium via `tauri-apps/cef-rs`, embedded in-process
inside the Tauri main window.** Single engine for both user-facing
browsing and agent automation. CDP pipeline carries forward unchanged
from ADR-0010's brain side; the Rust-side process supervisor is replaced
by an in-process CEF lifecycle owner. ADR-0010 is **superseded** by
ADR-0019 (this spike's companion); the sidecar process supervisor and
the discovery/spawn/exit logic in `crates/<browser>` retire with it.

### What the rewrite actually builds (browser slice)

- **Engine.** `tauri-apps/cef-rs` pinned to a CEF-147 release; bumped
  per Chromium minor with the existing dependency-review cadence.
  Chrome bootstrap + Alloy style (M125+ unified). Off-Screen Rendering
  is *not* enabled in the windowed path; it remains an option inside
  the Linux/Wayland fallback story.
- **Embedding.** CEF's Browser host parented to a child `NSView` /
  `HWND` / `GtkWidget` of the Tauri main window — sized and positioned
  by the renderer (the drawer geometry). The renderer never paints
  the surface; CEF paints directly into the parented native view.
  This is in-process embedding, not cross-process re-parenting; the
  prior spike's failure modes do not apply.
- **User browsing surface.** The in-app browser drawer (per v2 §5.2)
  hosts the CEF view with our own thin chrome (back / forward / URL /
  reload / "Open in system browser" escape). Cookies and session
  state persist in a per-Thalyn Chromium profile under the app data
  dir (carries forward from ADR-0010 unchanged in shape, just located
  in-process).
- **Agent automation.** Brain spawns CDP via the same
  `--remote-debugging-port=0` + `DevToolsActivePort` discovery path,
  attaches over WebSocket. The brain's `CdpConnection` and the five
  `browser_*` tools (`navigate`, `get_text`, `click`, `type`,
  `screenshot`) are unchanged. The only difference is the WS URL
  comes from the in-process CEF instance, not a sidecar.
- **Per-step capture.** Carries forward from ADR-0010's v0.13
  refinement: action-log replay writes DOM snapshot + PNG to
  `runs/{run_id}/browser/<seq>.{html,png}`. Implementation moves into
  the CEF lifecycle owner; the runner-facing `set_capture_dir` API
  is unchanged.
- **Take-over / intervention.** No longer needs OS-window-raise
  plumbing — the surface is already inside the Tauri window. Take-
  over becomes a chat affordance ("pause agent, hand keyboard back
  to user"); Resume picks up against whatever URL the user navigated
  to. The OS-specific `NSWindow.makeKeyAndOrderFront` / wlr-foreign-
  toplevel code planned in the prior spike is deleted, not built.
- **OAuth.** Default path: CEF surface, Chrome-style UA. Single-click
  "Open in system browser" affordance on each OAuth step as the
  user-driven escape hatch.

### What the rewrite does *not* build

- The v1 sidecar process supervisor, profile-discovery (find
  Chrome / Chromium / Edge / Brave on disk), `THALYN_BROWSER_BIN`
  env var override, `DevToolsActivePort` poll loop, kill-trigger
  watcher, OS-window-raise (`NSWindow` / `SetForegroundWindow` /
  `wlr-foreign-toplevel`). All retire with ADR-0010.
- A custom React browser chrome beyond the thin drawer chrome above
  (no tabs, no bookmarks bar, no extensions UI in v1). Tabs may land
  later as a v1.x affordance; we don't pre-build.
- A second, hidden, headless Chromium for agent flows. The same
  in-process CEF instance serves both the user view and the agent
  CDP target. The prior spike's separate-window observability
  console isn't needed because the user view *is* the surface.
- CEF native Wayland embedding. v1 ships X11/XWayland on Linux
  and adds a row to `docs/going-public-checklist.md`.

### Plan adjustments

- **ADR-0010 → status: Superseded by ADR-0019.** Mark on accept of
  ADR-0019. The provisional refinements (v0.13 spike retirement;
  discovery + lifecycle; brain CDP transport; renderer surface;
  per-step capture) become historical context — the brain CDP
  transport survives unchanged, the renderer surface is replaced
  by the drawer-hosted CEF view, the rest retires.
- **`02-architecture.md` §12 risk #1** stays retired by the prior
  spike; this spike adds a new (smaller) risk: *CEF native Wayland
  embedding is not yet shipped; X11/XWayland is the v1 Linux path*.
  Going-public-checklist gets a row.
- **Bundle.** Installer grows by ~130 MB compressed / ~250 MB on
  disk per platform. This was anticipated in the
  `project_no_external_apps` memo's option matrix and is the
  documented price of the hard rule. Atlas, Comet, Dia, and the
  Electron pack all carry the same.
- **Brain slice.** No changes. `CdpConnection` and the five
  `browser_*` tools carry forward unchanged.
- **Going-public-checklist.** Add: CEF security-update SLO (track
  Chromium-stable CVEs and ship a CEF bump within N days); CEF
  native Wayland support row; bundle-size review row; CEF profile
  encryption-at-rest row (for cookies / login state).

## Risks not retired

- **Maintenance burden of in-tree Chromium.** Real and ongoing.
  Chromium ships ~6-week cycles; CEF and cef-rs follow. We will
  need a quarterly dependency-review pass that includes a CEF/
  Chromium bump. The going-public-checklist should make a CVE-
  response SLO explicit (e.g. a Chromium-stable security advisory
  → a Thalyn release within the same week). This is a real cost
  the spike accepts; the alternative is paying it elsewhere
  (system-browser open for OAuth → user friction; system-webview
  → broken auth) — so we pay it where it does the most good.
- **Bundle install time on slow connections.** ~130 MB download
  per platform is real. First-run flow (`project_first_run_flow`)
  targets <90 s for Claude Code path; that target is post-install.
  Installer download is a separate measurement and worth a single
  perf check during the rewrite's installer commit.
- **CEF on Wayland for the public release.** Not a v1-blocker
  (X11/XWayland handles every shipping Wayland session) but it
  is an open Chromium-side commit. Track the CEF Wayland
  embedded-toplevel issue and re-evaluate quarterly in the
  dependency review.
- **OAuth heuristic detection.** A small number of IdPs may
  refuse CEF even with Chrome-style UA. The user-driven
  "Open in system browser" escape is the documented fallback;
  if more than ~10% of supported connectors trip it, revisit
  whether to invest in deeper CEF-as-Chrome spoofing (Chrome API
  key handling; Chromium build tooling) — but the bar for that
  investment is empirical, not preemptive.
- **CEF profile data-at-rest.** Cookies, login state, and form
  history live in the per-Thalyn profile under the app data dir.
  Encryption-at-rest is the going-public bar (parity with the
  user's main browser); ship plaintext for v1 with a checklist
  row, encrypt before public.

## Sources

- `project_no_external_apps` — agent-memory entry recording the *user never leaves the app* hard rule.
- [04-vision-v2.md §0 hard rule](../../04-vision-v2.md)
- [ADR-0010 — sidecar headed Chromium](../adr/0010-browser-sidecar-chromium-cdp.md)
- [Prior retired spike — re-parenting](2026-04-26-webview-chromium-reparenting.md)
- [tauri-apps/cef-rs (releases)](https://github.com/tauri-apps/cef-rs/releases)
- [cef-rs latest: cef-v147.1.0+147.0.10 (2026-04-26)](https://github.com/tauri-apps/cef-rs/releases/tag/cef-v147.1.0%2B147.0.10)
- [chromiumembedded/cef on GitHub](https://github.com/chromiumembedded/cef)
- [Tauri webview versions reference](https://v2.tauri.app/reference/webview-versions/)
- [Tauri-Apps tauri#10079 — child webviews on a window](https://github.com/tauri-apps/tauri/issues/10079)
- [tauri-apps/wry#703 — CEF as a universal fallback (closed, low-priority)](https://github.com/tauri-apps/wry/issues/703)
- [Google — Upcoming OAuth changes for embedded webviews](https://developers.googleblog.com/upcoming-security-changes-to-googles-oauth-20-authorization-endpoint-in-embedded-webviews/)
- [Auth0 — disallowed_useragent for embedded browsers](https://community.auth0.com/t/403-disallowed-useragent-for-web-login-from-embedded-browsers/55074)
- [Google — Block sign-ins from Chromium embedders (CEF named)](https://groups.google.com/a/chromium.org/g/embedder-dev/c/STyM5ZNTHMM)
- [RFC 8252 native-app OAuth — loopback + system browser pattern](https://developers.google.com/identity/protocols/oauth2/native-app)
- [Playwright — only WebView2 supports CDP among system webviews](https://playwright.dev/docs/browsers)
- [tauri-plugin-playwright (in-app control server, not CDP)](https://lib.rs/crates/tauri-plugin-playwright)
- [Servo 0.1.0 — embeddable, with web-compat caveats](https://servo.org/)
- [Verso archived; couldn't keep pace with Servo revisions](https://github.com/versotile-org/verso)
- [Ultralight licensing — proprietary, $100K cap, closed core](https://ultralig.ht/)
- [Sciter licensing — $310 commercial; not a general-web engine](https://sciter.com/prices/)
- [Phoronix — CEF Wayland progress, Toyota-sponsored](https://www.phoronix.com/news/Chromium-CEF-Wayland-Progress)
- [chromiumembedded/cef#2804 — embedded Ozone/Wayland tracking issue](https://github.com/chromiumembedded/cef/issues/2804)
- [CEF Forum — Chrome runtime vs Alloy runtime, M125+ unified](https://www.magpcss.org/ceforum/viewtopic.php?f=17&t=18750)
- [Electron WebContentsView migration guide (rejected substrate path)](https://www.electronjs.org/blog/migrate-to-webcontentsview)
- [BrowserOS — open-source Chromium fork with native AI agents](https://github.com/browseros-ai/BrowserOS)
- [Comet (Perplexity) — Chromium-fork architecture for agentic browsing](https://en.wikipedia.org/wiki/Comet_(browser))
