# Going-Public Checklist

This is the single living document tracking everything that must happen before Thalyn is published for installation by anyone who isn't the developer. It exists because v1's threat model (`01-requirements.md` §10.1 OQ-2) was deliberately scoped to development / personal-use; broader distribution re-opens questions that were closed under that scope.

This file is updated as Thalyn evolves. At any moment, it answers the question: *"What do we still need before going public?"*

## Why this checklist exists

When Thalyn is installed only by its developer, threats like targeted attackers, supply-chain APT-level adversaries, and hostile housemates aren't credible. Once anyone else installs Thalyn, those threats become credible — and decisions we deferred (code-signing, hash-chained logs, an SBOM, a vulnerability disclosure process) re-enter scope.

This document does not block v0.x progress. It blocks the *publish* button.

---

## Items

Each item links to the requirement / ADR it overrides or extends.

### Threat model
- [ ] **Re-confirm threat model.** Run a STRIDE pass with the broader audience in mind. Update `01-requirements.md` §10.1 OQ-2 and ADR-0011 (sandbox tiers) accordingly.
- [ ] **Audit network egress defaults** for connectors and MCP servers — anything overly permissive must be tightened. (MCP connectors today inherit the user's network; the marketplace surfaces vendor + homepage so the user can audit before installing, but there is no per-connector egress allowlist yet.)
- [ ] **Prompt-injection sanitizer review.** v1 ships a light defense (F7.5); a targeted-attacker model warrants a heavier review.
- [ ] **Email send-gate audit.** The double-gate (renderer modal + brain refusal) covers honest mistakes and unattended schedules; before going public, audit that no other code path can issue `email.approve_draft` without a user click.

### Distribution & signing
- [ ] **Apple Developer Program enrollment** ($99/yr) for Developer ID + notarization on macOS.
- [ ] **Windows code-signing.** Either Authenticode certificate (~$200–$700/yr) or SignPath / Microsoft Trusted Signing free tier where eligible.
- [ ] **Linux signing.** Sigstore for `.deb` / `.rpm` / AppImage; consider distro repository submission only after a real user base exists.
- [ ] **Reproducible builds** for the Tauri and Python-sidecar artifacts where the toolchain allows.
- [ ] **SBOM (CycloneDX)** generated automatically on each release; published alongside the artifacts.

### Supply chain
- [ ] **Dependency pinning by hash** in `Cargo.lock`, `pnpm-lock.yaml`, and the Python sidecar's lockfile (already in v0.x).
- [ ] **Dependency-update policy** (Renovate / Dependabot config), with a human-review gate before merge.
- [ ] **Vendored MCP servers** — first-party connectors are vendored or version-pinned by hash; community connectors are gated behind explicit user installation. (Today the first-party Slack / Office / Calendar descriptors point at `npx @modelcontextprotocol/server-*` invocations; vendoring or hash-pinning those packages is still pending.)

### Disclosure & community
- [ ] **`SECURITY.md`** — coordinated-disclosure policy, private contact channel, 90-day public-disclosure default.
- [ ] **Decide DCO vs CLA** (deferred from `01-requirements.md` §10.2 OQ-11). Recommend DCO.
- [ ] **`CODE_OF_CONDUCT.md`** — Contributor Covenant or equivalent.

### Licensing
- [ ] **Re-evaluate MIT vs Apache-2.0** (`01-requirements.md` §10.1 OQ-7, ADR-0016). Apache-2.0's patent grant matters more once the project ships broadly.

### Audit logs
- [ ] **Hash-chained / signed audit logs** (currently F7.6 ships unsigned NDJSON). Upgrade to a tamper-evident format.

### Voice input
- [ ] **CEF media-permission UX wiring.** v1 captures the composer mic in the Rust core, not in CEF, so the in-app browser doesn't request microphone access. Before a public release ships any in-CEF voice flow (e.g. a web app the user opens that wants `getUserMedia`), wire the `enable-media-stream` switch and implement `OnRequestMediaAccessPermission` + `OnShowPermissionPrompt` callbacks to surface the request through Thalyn's own UI rather than dropping it on the floor. (ADR-0025; ADR-0019.)
- [ ] **PipeWire / `xdg-desktop-portal` audio portal integration on Linux.** v1 captures audio via ALSA / PulseAudio direct, which works on every shipping distro today but doesn't fit the sandboxed-client model that the audio portal is moving toward. Track the portal's distro-coverage and switch the Linux audio path when the portal is ubiquitous on supported distros. (ADR-0025; PipeWire portal docs.)
- [ ] **Apple Silicon Core ML acceleration for Whisper.** `whisper-cpp-plus` 0.1.4 wraps `whisper.cpp` but does not yet expose the upstream `WHISPER_COREML=1` build flag as a cargo feature. v1 ships Metal acceleration via `voice-whisper-metal`, which already hits the Apple-Silicon latency budget in ADR-0025; the ANE path drops encoder runtime another 3–6× on top. Either upstream a `coreml` cargo feature to `whisper-cpp-plus` or carry an in-tree `build.rs` patch + the matching `.mlmodelc` artifact alongside each `.bin` model. (ADR-0025; whisper.cpp PR #566.)
- [ ] **Cross-platform voice latency confirmation** on the hardware ADR-0025 names but the dev box can't reach: M-series Macs other than M4 (the spike's M1-baseline number is interpolated from M4 + published M1 numbers, not measured on M1 directly), a representative Linux box without GPU, and the 2-year-old Windows laptop slot. The bake-off harness at [`docs/spikes/voice-bake-off/`](spikes/voice-bake-off/) re-runs in one command; revise the ADR-0025 latency budgets if the measured numbers diverge. (ADR-0025; spike F2.)
- [ ] **Deepgram Nova-3 cloud-fallback live smoke.** v1 wires the routing surface and capability-delta UX behind a settings flag; the actual streaming WebSocket path is exercised by unit tests but not against a real Deepgram endpoint (the dev environment has no API key). Confirm sub-300 ms time-to-final on a wired connection with a real key before the public release flips the cloud path on by default. (ADR-0025.)
- [ ] **MLX-Whisper opt-in real wire-up.** v1 ships the settings flag and the routing surface but doesn't pull MLX into the brain venv (MLX is Apple-Silicon-only and the bundle weight cost is ~600 MB on top of the existing models). Wire the dep + the model-download path before recommending MLX as a power-user opt-in in the user-facing docs. (ADR-0025.)
- [ ] **small.en runtime lazy-download UI.** v1 ships `base.en` preloaded inside the bundle (148 MB) for immediate-first-use; the larger `small.en` (487 MB) catalogs in [`src-tauri/src/voice/models.rs`](../src-tauri/src/voice/models.rs) with its pinned URL + SHA-256 but doesn't yet wire the runtime HTTP-streaming path. Add a settings-side "download `small.en` for higher accuracy" flow with progress reporting; verify SHA-256 on completion before swapping the file in. The catalog metadata is already in place; the remaining surface is the HTTP client + the renderer-side progress UI. (ADR-0025.)

### Browser engine (CEF)
- [ ] **CEF / Chromium-stable CVE response SLO.** Define and publish the
  service-level objective from a Chromium-stable security advisory to a
  shipped Thalyn release with the matching CEF bump. ADR-0019's
  maintenance-burden note flags this; a published target (e.g.
  `Chromium-stable advisory → Thalyn release within 7 days`) is the
  going-public bar.
- [ ] **CEF native Wayland embedded-toplevel support.** v1 ships
  CEF with `ozone-platform=x11` (X11/XWayland) on Linux; native
  Wayland embedded-toplevel is on the CEF roadmap
  ([chromiumembedded/cef#2804](https://github.com/chromiumembedded/cef/issues/2804))
  but not yet shipped. Switch when it lands and document the
  Wayland-native install path.
- [ ] **CEF bundle-size review at release.** Installer growth from
  the engine swap is documented at ~130 MB compressed / ~250 MB on
  disk per platform. Re-measure at release-cut and compare against
  the documented budget; surface if it has drifted.
- [ ] **Brain-sidecar bundle-size review at release.** PyInstaller's
  one-folder bundle of the brain comes in at ~260 MB on disk on
  macOS arm64 — about 2.5× ADR-0018's ~100 MB estimate, driven by
  the langgraph + claude-agent-sdk + opentelemetry stack. Re-measure
  at release-cut; the obvious mitigations are
  `--collect-submodules`'d binaries we don't actually use at
  runtime and dropping unused submodules of the heavy deps.
- [ ] **CEF profile encryption-at-rest.** v1 stores the per-Thalyn
  Chromium profile (cookies, login state, form history) as
  plaintext under the app data dir. Public-release bar is
  encryption-at-rest parity with the user's main browser; OS-keychain-
  wrapped DEK is the likely shape.
- [ ] **Windows native-view parenting (real impl).** macOS ships
  the parented `NSView` path; Windows currently compiles as a
  cfg-gated stub (no `SetParent` call, null `HWND`). Real wiring
  reads the Tauri main window's `HWND` via
  `WebviewWindow::hwnd()`, passes it as
  `cef_window_info_t::parent_window`, and tracks the drawer-host
  rect via `SetWindowPos`. Verification needs a Windows box.
- [ ] **Linux X11 native-view parenting (real impl).** Same shape
  as Windows: cfg-gated stub today (zero `Window` handle). Real
  wiring is GtkSocket/XEmbed under the Tauri main window's GTK
  widget; ADR-0029 §5 describes it. Verification needs a Linux
  desktop environment, not just the CI Linux build.

### Eternal-thread durability
- [ ] **30-min duration-based soak gate.** The per-push CI gate runs the randomized-kill soak count-bounded (default 200 iterations, ~30s); a public release needs the same harness scheduled nightly with `THALYN_SOAK_DURATION_SECS=1800` so the 30-minute exposure window the v2 build plan calls for is exercised continuously. (ADR-0022's "Alternatives considered" section captures the trade-off.)
- [ ] **Power-cut-grade durability test.** The current soak asserts SQLite-transaction atomicity at the application layer. A public-release-grade gate needs an OS-level kill (real `kill -9` between SQLite's commit and the disk's fsync barrier) before claiming the class-A correctness invariant under hardware failure modes.

### Telemetry & error reporting
- [ ] **One-click Sentry OAuth** UX (currently paste-your-own-DSN per OQ-13). Polish.

### Operational
- [ ] **Public release process** — release notes generated by `git-cliff`, signed tags, attached SBOMs and signatures.
- [ ] **Brand and identity** — logo, domain, GitHub org, Discord/forum or stated "issues only," contributor recognition.
- [ ] **Funding posture** — decide on / against GitHub Sponsors (`01-requirements.md` §10.3 OQ-14).
- [ ] **Security review pass.** Internal review by author; external review if warranted by complexity / user base size at the time.

### Documentation
- [ ] **User-facing docs site** — Astro Starlight per ADR-0014, deferred from v0.x.
- [ ] **Installation guides** for macOS, Linux, Windows including signature verification.
- [ ] **A "what Thalyn does and doesn't see"** page — explicit privacy statement for end users.

---

## Process

The checklist is reviewed at every per-release architecture review (`01-requirements.md` F12.4). Items don't move from "to do" to "done" without a corresponding commit (and ADR if it supersedes a v1 decision).

When the checklist hits "all items done," cutting a public release is one tagged push.
