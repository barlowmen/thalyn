# ADR-0029 — In-process CEF embedding: tao integration via runtime swizzle

- **Status:** Accepted
- **Date:** 2026-04-30
- **Deciders:** Barlow
- **Supersedes:** —
- **Superseded by:** —
- **Refines:** ADR-0019

## Context

ADR-0019 chose `tauri-apps/cef-rs` as the in-app browser engine and
declared CEF would run **in-process inside the Tauri main window**.
The 2026-04-30 spike
(`docs/spikes/2026-04-30-cef-macos-message-loop.md`) surfaced the
load-bearing macOS obstacle: tao (Tauri's windowing crate) and CEF
both register their own `NSApplication` subclass, both override
`sendEvent:`, and macOS allows exactly one such subclass per
process. The spike's first recommendation — defer the conflict by
running CEF in a child binary parented to the Tauri window via
`NSWindow.addChildWindow:` — fell when the v0.29 implementation
discovered modern macOS does not support cross-process window
hosting (CEF maintainer Marshall Greenblatt on the [CEF
Forum](https://magpcss.org/ceforum/viewtopic.php?f=6&t=19593),
Chromium engineer Nico Weber on
[chromium-dev](https://groups.google.com/a/chromium.org/g/chromium-dev/c/H5fQcXllT3E),
Apple's own Java Plugin2 abandonment all align). The spike report's
refinement section reaches the same conclusion and points at this
phase. ADR-0019's revised refinement section also tracks the unwind.

The conflict is not specific to v0.29's transitional `[[bin]]`. Any
future where CEF's browser process and Tauri's main process share
the same address space — the only future ADR-0019 leaves open —
needs:

1. One NSApplication subclass that satisfies both contracts, and
2. A path that lets that subclass be the principal class instead of
   tao's `TaoApp`.

(1) is direct: cefsimple's `SimpleApplication` shape is the
reference; tao's `TaoApp` adds a CMD-key-up `sendEvent:` workaround
on top of stock NSApplication; the two are compatible because the
CEF flag-toggle wraps the call to `super::sendEvent:` and the tao
workaround dispatches to the key window from the same hook. The
v0.29 child binary already implements (1) for the cefsimple side as
`cef::child::mac::ThalynChildApplication`; folding tao's
`sendEvent:` onto that subclass is a small extension.

(2) is the hard problem and the substance of this ADR.

Three credible integration paths exist:

- **Runtime swizzle.** Add CEF's `CefAppProtocol` /
  `CrAppProtocol` / `CrAppControlProtocol` methods (and the
  `handling_send_event` ivar plus the `terminate:` and `sendEvent:`
  overrides) to tao's `TaoApp` class **at runtime**, after tao
  registers it but before `cef::initialize` runs. java-cef ships a
  variant of this pattern as a fallback for embedders that cannot
  subclass NSApplication directly.
- **Vendored tao fork.** Maintain `vendor/tao/` in our tree with a
  `[patch.crates-io] tao = { path = ... }` entry in the workspace
  Cargo.toml. The fork uses our `ThalynApplication` subclass
  directly. We carry the diff against upstream tao at every Tauri
  2 minor.
- **Upstream tao API.** File a PR for
  `EventLoopBuilder::with_application_class(...)` that lets host
  apps inject the NSApplication subclass. Clean shape; defensible
  rationale (other apps will hit the same conflict eventually);
  multi-month landing horizon given the surface and the review
  load — every macOS Tao consumer would be touched.

The tao integration choice has to be made early in v0.30 because
every other piece of in-process work is downstream of it: the
`main()` init sequence, the `CefHost::start` reshape, the
helper-bundle layout, and the test harness all change shape per
the chosen path.

## Decision

Adopt **runtime swizzling** as the in-tree tao integration path
for v0.30. File the upstream
`EventLoopBuilder::with_application_class` PR as a parallel track;
do not block v0.30 on it landing.

Concrete shape:

### 1. Combined NSApplication subclass: `ThalynApplication`

A single Objective-C class registered at runtime that:

- **Inherits from `NSApplication`** and is the principal class for
  the entire Thalyn process — both browser + agent flows and
  Tauri's window/menu/tray/dock surfaces dispatch through it.
- **Implements `CefAppProtocol`, `CrAppProtocol`,
  `CrAppControlProtocol`** with a `handling_send_event` boolean
  ivar matching the cefsimple shape. CEF's macOS message-pump
  observer queries `isHandlingSendEvent` during dispatch; without
  these protocols CEF crashes with
  `Check failed: nesting_level_ != 0` on the first event.
- **Overrides `sendEvent:`** with a single body that does both
  contracts in one pass: toggle the CEF flag around the
  `super::sendEvent:` call, AND apply tao's CMD-key-up workaround
  ([upstream comment](https://stackoverflow.com/a/15294196) cites
  the longstanding macOS bug). The two overrides commute — CEF's
  flag-toggle wraps the super call; tao's workaround conditionally
  dispatches to the key window before yielding to super — so a
  single combined implementation is correct.
- **Overrides `terminate:`** to route Cocoa's orderly-quit
  machinery through CEF's `CloseAllBrowsers`. The default
  `[NSApplication terminate:]` calls `exit()`, which cuts off CEF
  shutdown; the override calls `CloseAllBrowsers(false)` and lets
  the message loop drain via `quit_message_loop()` once the last
  browser closes (matching cefsimple's `SimpleHandler` path). When
  no CEF session is live the override falls through to
  `super::terminate:` so Tauri's quit handling proceeds normally.

The protocol implementations are lifted directly from v0.29's
`src-tauri/src/cef/child/mac.rs`. The tao `sendEvent:` workaround
is added on top.

### 2. Class registration at runtime, before `cef::initialize`

The principal class is registered **after** tao registers `TaoApp`
and **before** `cef::initialize` runs. The registration uses raw
Objective-C runtime calls (`class_addMethod`,
`class_addProtocol`, `method_setImplementation`,
`objc_setAssociatedObject` / `objc_getAssociatedObject`) to:

1. Look up the `TaoApp` class object that tao has already created.
2. Store the `handling_send_event` boolean in an **associated
   object** keyed by a static address. The Objective-C runtime
   only allows `class_addIvar` between `objc_allocateClassPair`
   and `objc_registerClassPair`; tao has long since registered
   `TaoApp` by the time the swizzle runs, so a real ivar is not
   reachable. Associated objects are the runtime-supported
   sidecar for this exact case — every call to
   `setHandlingSendEvent:` / `isHandlingSendEvent` performs one
   hash-table lookup keyed by the (object, key) pair, which is
   negligible against the per-event cost CEF already pays.
3. Add the `setHandlingSendEvent:` / `isHandlingSendEvent`
   methods (the `CrAppControlProtocol` / `CrAppProtocol` impls)
   that read and write the associated object.
4. Add conformance to `CefAppProtocol`, `CrAppProtocol`, and
   `CrAppControlProtocol`. The CEF runtime check uses
   `conformsToProtocol:`, which walks the registered protocol
   list — `class_addProtocol` is enough; method-level
   conformance is established by step 3.

   **Caveat — `CefAppProtocol` is application-defined.** Chromium's
   framework binary ships `CrAppProtocol` and `CrAppControlProtocol`
   in `__DATA_CONST,__objc_protolist`, so dlopen registers them with
   the runtime. `CefAppProtocol` is the umbrella marker every CEF
   embedder is *expected to define itself* (it appears in
   `cef_application_mac.h` only as `@protocol CefAppProtocol
   <CrAppControlProtocol> @end`). cef-rs's `extern_protocol!` macro
   declares the Rust trait shape but emits no Objective-C metadata,
   so `<dyn CefAppProtocol>::protocol()` returns `None` until the
   embedder allocates the protocol via `objc_allocateProtocol` /
   `protocol_addProtocol(parent = CrAppControlProtocol)` /
   `objc_registerProtocol`. The swizzle does that before
   `class_addProtocol(CefAppProtocol)` — without it CEF aborts on
   the first event.
5. Replace `sendEvent:` with the combined override
   (`method_setImplementation` against the existing IMP, with the
   replacement IMP wrapping `super::sendEvent:` in the CEF
   flag-toggle while preserving tao's CMD-key-up workaround). The
   original IMP is captured via `class_getMethodImplementation`
   before replacement so the new body can call it as `super`.
6. Replace `terminate:` with the `CloseAllBrowsers` reroute (same
   `method_setImplementation` shape).

The hook fires in `tauri::Builder::setup`. By that point Tauri's
runtime has built the EventLoop (so `TaoApp` exists and `NSApp` is
the locked-in `TaoApp` instance) but the AppKit run loop has not
yet spun. This is the only safe window: earlier, `TaoApp` does not
exist yet; later, the message-pump observer is already running
against an NSApp that does not implement `CefAppProtocol`.

### 3. Pre-`tauri::Builder` initialization in `main()`

CEF requires `LibraryLoader::load` and `cef::execute_process` to
run before any AppKit code in the browser process — the loader
maps `Chromium Embedded Framework.framework` and
`execute_process` short-circuits helper-process invocations
(`type=renderer`, `type=gpu-process`, etc.) that re-exec the
parent binary. The order in `main()` is therefore:

```rust
fn main() {
    // 1. Map the CEF framework before any AppKit symbol resolves.
    let _library_loader = cef::library_loader::LibraryLoader::new(
        &std::env::current_exe().expect("current_exe"),
        /* helper = */ false,
    );
    assert!(_library_loader.load());

    // 1a. Negotiate the API version with the framework. CEF's
    //     wrapper structs carry an inline size header that the
    //     framework cross-checks against; without an `api_hash` call
    //     after `LibraryLoader::load` the first internal call into
    //     a wrapped App / Client / handler fails with `CefApp_0_CToCpp
    //     called with invalid version -1`.
    let _ = cef::api_hash(cef::sys::CEF_API_VERSION_LAST as i32, 0);

    // 2. Helper-process branch. Returns -1 in the browser process;
    //    helpers run their subprocess work and exit here. Helpers
    //    must NOT continue on to tauri::Builder.
    let exit_code = cef::execute_process(
        Some(&cef::args::Args::new().as_main_args()),
        None::<&mut cef::App>,
        std::ptr::null_mut(),
    );
    if exit_code >= 0 {
        std::process::exit(exit_code);
    }

    // 3. Browser process: hand off to Tauri. Tauri's setup hook
    //    swizzles ThalynApplication onto TaoApp, then calls
    //    cef::initialize before the run loop spins.
    thalyn_lib::run();
}
```

The library loader is held by `_library_loader` for the lifetime
of the process; dropping it would unmap the framework while CEF
still needs it.

### 4. `cef::initialize` inside Tauri's setup hook

Inside `tauri::Builder::setup`:

1. Swizzle `ThalynApplication` onto `TaoApp` (per §2 above).
2. Call `cef::initialize(...)` against the per-Thalyn profile
   (`cache_path` and `root_cache_path` set to
   `<data_dir>/cef-profile`, `remote_debugging_port = 0`).
3. Subscribe to the `DevToolsActivePort` watcher just as v0.29's
   port-file path did, and surface the WS URL through `CefHost`'s
   existing state machine. The brain still attaches via
   `browser.attach` over JSON-RPC; the only difference is the WS
   URL points at the in-process CEF instance now.
4. Schedule `cef::shutdown` on the `WindowEvent::CloseRequested`
   path so kill-and-relaunch is clean.

`cef::initialize` is process-global, so a single `CefHost`
instance manages it. `CefHost::start` reshapes from "spawn child +
watch port file" to "create CEF Browser + parent its native view";
the public surface (`HostState::Idle | Starting | Running |
Exited`, `start`, `stop`, `set_window_rect`) is unchanged so the
renderer layer sees no wire diff.

### 5. Native-view parenting

CEF Browser instances are parented to a child native view of the
Tauri main window's drawer-host region. Per platform:

- **macOS.** The browser drawer's React surface mounts a
  fixed-size placeholder div whose absolute rect is reported via
  the existing `cef_set_window_rect` Tauri command (landed in
  `afd5b72`). The parenting layer creates an `NSView` child of
  Tauri's `contentView`, sets its frame from
  `CefSession::current_window_rect`, and hands the view's
  `NSView*` handle to `cef_window_info_t::parent_view`. CEF
  paints into it via the platform compositor; passkey UI, IME
  preedit, drag-drop, and DRM video all use the platform input
  client / GPU surface they need.

  Implementation notes. The host view is owned by a process-global
  `OnceLock` in `crate::cef::embed::host_view`, installed once from
  inside the Tauri setup hook (the only safe AppKit-mutation point
  before the run loop spins). A `cef::App` handler reads the host
  view via `current_handle()` from
  `BrowserProcessHandler::on_context_initialized` and calls
  `browser_host_create_browser`. Subsequent rect updates from
  `CefHost::set_window_rect` are dispatched to the main thread via
  `dispatch_async_f` against `_dispatch_main_q` (libdispatch's
  `dispatch_get_main_queue()` macro is not a real symbol, so we
  bind to the queue object directly). The y-axis is flipped from
  HTML's top-origin to AppKit's bottom-origin using the parent
  view's current bounds height, read on the main thread.
- **Windows.** Equivalent path with `HWND` child via `SetParent`.
  `cef_window_info_t::parent_window` carries the parent handle.
- **Linux X11.** XEmbed protocol via `GtkSocket`. Cross-process
  embedding works on X11 (unlike macOS); the input-routing edge
  cases the spike flagged are real but addressable. If GtkSocket
  trips them, the fallback is a free-standing X11 toplevel
  parented via `XReparentWindow` for Linux only, with the caveat
  documented next to the Wayland row on the
  going-public-checklist.
- **Linux Wayland.** Native embedded toplevels stay on the
  going-public-checklist, with the X11/XWayland path serving
  Wayland sessions in the meantime (CEF
  `ozone-platform=x11`).

The renderer rect plumbing landed in v0.29 is the durable
contract: `CefSession::current_window_rect` returns the latest
absolute rect in CSS pixels (== macOS points at devicePixelRatio
2), and the parenting layer applies it on the native view every
time it changes — drawer width drag, drawer hide, drawer close,
and chat-area focus all flow through the same path.

### 6. macOS helper-bundle structure

CEF's multi-process model on macOS requires a specific bundle
layout under `<App>.app/Contents/Frameworks/`:

```
Contents/
  Frameworks/
    Chromium Embedded Framework.framework/
      Chromium Embedded Framework
      Libraries/
      Resources/
      Versions/
    Thalyn Helper.app/
    Thalyn Helper (GPU).app/
    Thalyn Helper (Renderer).app/
    Thalyn Helper (Plugin).app/
```

Each helper `.app` is a tiny bundle whose executable is the same
Thalyn binary invoked with `--type=renderer` (or `gpu-process`,
`utility`, etc.). cef-rs's `bundle-cef-app` tool produces these
from the parent binary; the v0.30 work integrates it into the
Tauri bundle pipeline (`src-tauri/tauri.conf.json` `beforeBundle`
hook or equivalent).

Helper-bundle codesigning. Each helper `.app` must be signed with
the same identity as the parent app, or the runtime aborts. Per
the going-public-checklist, codesigning is post-v1; v0.30 ships
unsigned helper bundles for development use, and the
checklist row gains a note that signing is a release-cut
prerequisite.

### 7. Retirement plan

In the same commit that lands `ThalynApplication`:

- Delete `src-tauri/src/bin/thalyn-cef-host.rs`.
- Delete `src-tauri/src/cef/child/` (mac.rs, app.rs, client.rs,
  mod.rs).
- Delete the `[[bin]]` `thalyn-cef-host` entry from
  `src-tauri/Cargo.toml`.

Once `CefHost::start` is reshaped to call `cef::initialize`
in-process and the renderer has been smoke-tested:

- Delete `src-tauri/src/browser/{discover,supervisor,mod.rs}`.
- Delete the `BrowserManager` field on `AppState`.
- Delete the `THALYN_BROWSER_BIN` env override and the
  well-known-paths Chromium discovery.
- Delete the OS-window-raise plumbing
  (`NSWindow.makeKeyAndOrderFront`, `SetForegroundWindow`,
  `wlr-foreign-toplevel`).

Once the dev-onboarding doc gains the cmake/ninja prerequisite
note:

- Flip the `cef` Cargo feature from optional to default-on at the
  parent crate. CI's existing `cef build (linux)` job becomes the
  only gate that exercises the feature; default `cargo check`
  also pulls cef-rs in. Default Cargo.toml profile stays as it is.

### 8. Test harness

`CefHost`'s existing tests (`host_starts_in_idle_state`,
`pending_rect_is_held_until_a_session_starts`,
`stop_when_idle_is_a_typed_error`) carry forward unchanged — they
exercise the public state machine, not the underlying engine. The
v0.29 child-binary smoke tests (`start_returns_running_when_…`,
`set_window_rect_lands_on_a_running_session`,
`start_surfaces_early_exit_when_…`) retire alongside the child
binary and are replaced by feature-gated in-process equivalents
that drive `CefHost::start` against a real `cef::initialize` on
the CI's `cef build (linux)` runner. The kill-and-relaunch test
becomes the load-bearing macOS verification for the
`terminate:` reroute and is run manually pre-tag because CI does
not run on macOS.

## Consequences

- **Positive.**
  - **Single-process from the user's perspective** — one `Thalyn`
    in `ps`, no separate `thalyn-cef-host` to spawn, no port-file
    handoff, no second Mach task to keep in sync.
  - **Native-view parenting** — drawer width drag, hide, close
    propagate to CEF in-process via `setFrame:` on the parented
    NSView. No cross-process IPC for geometry.
  - **Brain CDP unchanged** — the WS URL points in-process now
    but the JSON-RPC `browser.attach` and the five `browser_*`
    tools speak the same protocol they spoke against the child
    binary and the v1 sidecar.
  - **Substrate work isolated** — runtime swizzle keeps the
    upstream-tao diff at zero; the upstream API PR can land on
    its own timeline without blocking v0.30.
  - **v1 sidecar codepath retires** — the `BrowserManager`,
    discovery, supervisor, and OS-window-raise plumbing all go
    away. One engine instead of two.
- **Negative.**
  - **Runtime swizzle is fragile against tao internals.** The
    swizzle assumes `TaoApp`'s class layout and the semantics of
    its `sendEvent:` impl as it ships today. Every Tauri 2 minor
    that touches tao's macOS code carries a manual verification
    cost. The mitigation is the upstream
    `with_application_class(...)` PR — when it lands, the swizzle
    becomes throwaway and the integration moves to the API
    surface tao supports.
  - **Failure mode is process death without a useful stack
    frame.** A CEF protocol the swizzle doesn't add, or a tao
    update that changes `TaoApp`'s ivars, prints
    `Check failed: nesting_level_ != 0` and aborts. The mitigation
    is the kill-and-relaunch test plus a smoke test that `[NSApp
    isKindOfClass: NSApplication.class]` and conforms to all
    three CEF protocols at startup.
  - **cmake / ninja become developer prerequisites.** Once the
    `cef` feature is default-on, `cargo check` requires the CEF
    SDK and the bundled-cef build script. The mitigation is a
    `CONTRIBUTING.md` section explaining the one-time setup and
    the `THALYN_CEF_PATH` / `export-cef-dir` env knobs the build
    script honours.
  - **Helper-bundle codesigning is unsigned in v0.30.** Without
    the same identity as the parent app, helper processes work
    only on systems with developer-mode codesigning policy
    (developer machines). End users pre-v1 hit a gatekeeper
    error on launch. The mitigation is the
    going-public-checklist row that gates the v1 release on
    helper-bundle signing.
- **Neutral.**
  - **CEF Browser geometry is now Tauri-driven** — every drawer
    state change writes through `cef_set_window_rect` and the
    parenting layer applies it. The v0.29 round-trip (renderer
    → Tauri → port file → child) collapses to (renderer → Tauri
    → in-process). Latency drops; behaviour is identical.
  - **The `cef::child` module retires from the tree.** Its
    protocol-implementation work was always a substrate v0.30
    builds on; the in-process subclass replaces it. Git history
    preserves the v0.29 shape for readers tracing the engine
    swap arc.

## Alternatives considered

- **Vendored tao fork** under `vendor/tao/` with `[patch.crates-io]`.
  Rejected for v0.30. Owning a tao fork keeps the surface ours,
  but every Tauri 2 minor brings a rebase against upstream tao
  with potentially non-trivial conflict resolution against
  Wayland, Windows, and X11 paths the engine swap does not touch.
  Maintenance load > the upside relative to the runtime-swizzle
  patch's diff size. Acceptable as a fallback if the swizzle path
  hits a tao internal it cannot reach safely.
- **Upstream `EventLoopBuilder::with_application_class(...)` API.**
  Filed in parallel as the long-term clean shape. Rejected as the
  in-tree default for v0.30 because the landing horizon is
  multi-month: the surface touches every macOS Tao consumer (every
  Tauri 2 app), the review surface is large, and tao's release
  cadence is not under our control. v0.30 cannot block on it.
- **OSR (off-screen rendering) on macOS only.** Rejected.
  Reintroduces every screencast-as-primary failure mode the
  2026-04-26 spike documented (passkey UI, DRM, IME preedit,
  drag-drop). Stays a credible fallback if the helper-bundle
  codesigning story turns out worse than projected, but the
  default path is windowed.
- **Defer v0.30 entirely; ship the v0.29 child binary as v1.**
  Rejected. The v0.29 child-binary path's load-bearing
  cross-process parenting assumption is closed on modern macOS.
  Without `NSWindow.addChildWindow:` the v0.29 shape can only
  ship as a separate-OS-window app — exactly the violation of
  the user-never-leaves-the-app hard rule that drove ADR-0019 in
  the first place. The frame-tracking IPC fallback the spike's
  refinement floats is a 2-3-day patch but throwaway when
  in-process lands; pulling in-process forward is the right call.

## References

- ADR-0019 — *Browser engine: bundled Chromium via cef-rs*
  (refined by this ADR).
- 2026-04-30 spike — *cef-macos-message-loop* (with its
  post-investigation refinement section).
- 2026-04-27 spike — *browser-engine* (engine selection).
- 2026-04-26 spike — *webview-chromium-reparenting* (retired
  ADR-0010).
- cef-rs `examples/cefsimple/src/mac/mod.rs` — the cefsimple
  reference.
- tao `src/platform_impl/macos/app.rs` — the `TaoApp` class
  registration this ADR coexists with.
- CEF Forum, Marshall Greenblatt — [cross-process embedding on
  macOS](https://magpcss.org/ceforum/viewtopic.php?f=6&t=19593).
- chromium-dev, Nico Weber — [multi-process hosted browser window
  on Mac/Linux](https://groups.google.com/a/chromium.org/g/chromium-dev/c/H5fQcXllT3E).
