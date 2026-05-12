---
date: 2026-04-30
risk: ADR-0019 in-process CEF embedding requires sharing the macOS main thread with Tauri's runtime; both want to install a custom `NSApplication` subclass and override `sendEvent:` — head-to-head conflict that the engine-swap spike (`2026-04-27-browser-engine.md`) did not anticipate.
adr: 0019 (refines)
---

# Spike: cef-macos-message-loop

- **Question:** Under ADR-0019's *bundled CEF, in-process inside the
  Tauri main process* decision, how do we share the macOS main thread
  between Tauri (which installs a `TaoApp : NSApplication` subclass
  and overrides `sendEvent:` for a CMD-key-up workaround) and CEF
  (which requires a `SimpleApplication : NSApplication` subclass
  implementing `CefAppProtocol` / `CrAppProtocol` /
  `CrAppControlProtocol` and overrides `sendEvent:` / `terminate:`
  for orderly shutdown)?
- **Time-box:** 4 h. **Actual:** ~1 h (the macOS conflict surfaced
  decisively from one read of the cef-rs `cefsimple` macOS code and
  the corresponding tao macOS code; benchmarks not needed to call it).
- **Outcome:** Answered — the *clean* in-process path requires either
  forking Tao or upstreaming a CefAppProtocol layer; both options are
  multi-week. The pragmatic v0.29 path is the
  **child-process CEF + OS-window child-parenting** hybrid (Option D
  below), accepting per-platform parenting work and a known
  "child window, not embedded view" UX for v0.29 — with the actual
  ADR-0019-shaped in-process embedding deferred to a follow-on phase.

## Approach

1. Re-read ADR-0019 and the engine-swap spike
   (`docs/spikes/2026-04-27-browser-engine.md`). The spike scored
   options against six criteria and chose `tauri-apps/cef-rs`
   in-process embedding. It did **not** evaluate the macOS
   `NSApplication`-subclass collision between CEF and Tao because
   the spike's option matrix treated cef-rs's embedding viability
   as decisive at the protocol+binding shape, deferring perf and
   integration measurements to "when the panel is real."
2. Read the `cefsimple` macOS code in cef-rs:
   `examples/cefsimple/src/mac/mod.rs`. Confirmed the requirements:
   - A custom `NSApplication` subclass that implements three CEF
     protocols (`CefAppProtocol`, `CrAppProtocol`,
     `CrAppControlProtocol`).
   - That subclass overrides `sendEvent:` to track a
     `handling_send_event` flag CEF queries during dispatch.
   - That subclass overrides `terminate:` to redirect Cocoa's
     orderly-quit machinery through `CloseAllBrowsers` so CEF can
     shut down cleanly. The default `[NSApplication terminate:]`
     calls `exit()` — incompatible with CEF.
   - `setup_simple_application()` calls `[SimpleApplication
     sharedApplication]` *before any other code* touches `NSApp`, so
     the principal class lock-in happens against `SimpleApplication`.
3. Read tao's macOS code:
   `tauri-apps/tao/src/platform_impl/macos/app.rs`. Confirmed the
   collision:
   - tao registers a `TaoApp : NSApplication` subclass via
     `ClassDecl::new(superclass=NSApplication)` named `TaoApp`.
   - tao adds its own `sendEvent:` method to that subclass for a
     CMD-key-up workaround
     ([source comment](https://stackoverflow.com/a/15294196) cites
     the longstanding macOS bug also visible in Firefox).
   - tao calls `[NSApp sharedApplication]` against the registered
     `TaoApp` class during event-loop init, so the principal class
     lock-in happens against `TaoApp`.
4. Walked the CEF forum guidance ([viewtopic 19269](https://magpcss.org/ceforum/viewtopic.php?f=6&t=19269),
   [viewtopic 14862](https://magpcss.org/ceforum/viewtopic.php?f=6&t=14862)).
   Confirmed:
   - **Without `CefAppProtocol` on the active NSApplication, CEF
     crashes with `Check failed: nesting_level_ != 0`** during the
     macOS message-pump observer — not a recoverable error.
   - The recommended fix is "implement the protocols directly on
     your NSApplication subclass" (the cefsimple pattern); fallback
     is "protocol injection" (objc runtime swizzling) per the
     java-cef precedent.
   - `multi_threaded_message_loop` in `CefSettings` is documented as
     **not supported on macOS**.
   - `external_message_pump` works on macOS *if* `CefAppProtocol` is
     implemented, *but* host processes still need the protocol
     layer — it does not let us bypass the NSApplication-subclass
     requirement.
5. Considered five integration shapes (A, A.2, B.1, C, D). The
   findings below score them against the v0.29 exit criteria.

## Findings

### F1. The Tao + CEF NSApplication conflict is structural, not a tuning knob.

Both crates **register their own NSApplication subclass and override
`sendEvent:`**. The first call to `[NSApplication sharedApplication]`
locks in the principal class; whichever subclass is installed first
wins. There is no Cocoa mechanism to install two distinct
NSApplication subclasses in one process — the singleton is the
singleton.

Adding the CEF protocols to the existing TaoApp class at runtime via
objc swizzling is *technically* possible (java-cef ships exactly
that fallback) but the swizzle has to land *before* Tao's
`shared` is called *and* preserve Tao's `sendEvent:` workaround. The
combined class also has to override `terminate:` for CEF's orderly
shutdown without breaking Tauri's quit handling. This is a real
engineering exercise — not a one-line patch — and the surface
maintained against future Tao or CEF changes.

### F2. cef-rs assumes the cefsimple shape; deviating is the user's problem.

cef-rs's `library_loader::LibraryLoader` and `setup_simple_application()`
helpers expect to be called from a `main()` that owns the
NSApplication setup. There is no documented hook for "use this
existing NSApplication subclass instead." The `CefAppProtocol` /
`CrAppProtocol` / `CrAppControlProtocol` traits *can* be implemented
on a different subclass, but cef-rs ships no example of doing so
under another GUI framework's NSApplication.

In practice: any in-process Tauri+CEF integration on macOS requires
the integrating project to write its own `CombinedAppDelegate`,
`CombinedApplication` subclass, and the glue that wires them
together — and to maintain that against upstream changes in both
Tauri (Tao) and cef-rs.

### F3. Option A (combined NSApplication subclass, Tao fork) is the *correct* end state but multi-week work.

The clean shape: register one NSApplication subclass —
`ThalynApplication : NSApplication` — that:

1. Implements `CefAppProtocol`, `CrAppProtocol`,
   `CrAppControlProtocol` with the cefsimple-shape
   `handling_send_event` flag.
2. Implements tao's `sendEvent:` CMD-key-up workaround on top of
   the CEF flag-toggle (the two are compatible — the CEF flag
   wraps the call to `super::sendEvent:`, the tao workaround
   conditionally dispatches to the key window).
3. Overrides `terminate:` to call CEF's `CloseAllBrowsers`, then
   yields to tao/Tauri's quit handling.
4. Lives in our crate, not in tao.

Tao would need to be patched (or forked) to use our class instead of
its hardcoded `TaoApp`. Either:
- Upstream a tao API like `EventLoopBuilder::with_application_class(...)`
  that lets a host inject the NSApplication subclass — landing
  horizon measured in months given Tao's release cadence and the
  review surface (this would touch every macOS Tao consumer).
- Vendor a tao fork in our tree, paid for by ongoing rebase against
  upstream Tao at every Tauri 2 minor — material maintenance load
  for every other Tauri 2-on-macOS surface in our app (windowing,
  menus, tray, dock, activation policy).

Either way, this is a real piece of substrate work — multi-week,
not multi-day, and not a v0.29 deliverable.

### F4. Option A.2 (runtime swizzling) sidesteps the fork but is fragile and hard to test.

Use objc runtime swizzling to add `CefAppProtocol` /
`CrAppProtocol` / `CrAppControlProtocol` to `TaoApp` *after* tao
registers it but *before* `cef::initialize` runs. java-cef ships
this pattern as a fallback for embedders who can't subclass.

Trade-offs:
- Fragile against Tao's internal changes (the swizzle assumes the
  class layout and the `sendEvent:` semantics tao ships today).
- Hard to test cleanly — the swizzle modifies process-global state.
- Mixed-protocol implementations on a class we don't own carry an
  ongoing surface against both Tao and cef-rs upgrades.

It is technically a one-commit shape, but the support cost is real
and the failure mode (the `nesting_level_ != 0` crash) is process
death without a useful stack frame.

### F5. Option B.1 (CEF child + OSR + IPC frames) reintroduces the failure modes the prior spike retired.

CEF runs in a child process with **off-screen rendering**; the
parent process composites the rendered frames into a Tauri-native
view. No second NSApplication in our process. No native-view
re-parenting.

But the prior engine spike already enumerated the failure modes
this introduces:
- **Passkey / WebAuthn UI** lives in a real OS window. OSR cannot
  surface the platform credential picker reliably.
- **IME preedit composition** depends on AppKit's input client
  surface; OSR loses high-fidelity composition.
- **Drag-drop** between desktop and the rendered surface degrades.
- **DRM video** (Widevine) needs a real GPU compositor surface.

These were the *exact* reasons screencast-as-primary lost in the
2026-04-26 spike. Picking OSR for the user-facing browser surface
contradicts ADR-0019's load-bearing rationale.

OSR remains a credible **agent-only** path (the agent doesn't need
passkey UI) but the v0.29 exit criteria explicitly call for the
user-facing surface to log in to OAuth, drag-drop, watch video.

### F6. Option D (CEF child binary + OS child-window parenting) is the most pragmatic v0.29 deliverable.

Spawn a child binary `thalyn-cef-host` that:
- Owns its own NSApplication subclass (cefsimple-pattern, our crate).
- Initializes CEF with `--remote-debugging-port=0`.
- Renders to its own real Chromium window (full passkey / IME /
  drag-drop / DRM capability).

The Rust core in the parent process:
- Spawns and supervises the child, reads `DevToolsActivePort`,
  exposes the WS URL to the brain (CDP path unchanged from v1).
- Calls `NSWindow.addChildWindow:ordered:` to make the CEF window
  a *child* of the Tauri main window. macOS tracks child windows'
  position relative to the parent and manages Z-order as one unit
  — visually it appears as a panel attached to the Tauri window,
  not a separate app window.

What this delivers vs the ADR-0019 spec:
- ✅ User-facing surface keeps full Chromium capability (the
  load-bearing reason for ADR-0019).
- ✅ Brain CDP unchanged — same WS-URL plumbing.
- ✅ Per-Thalyn profile isolation.
- ✅ No external-Chromium *visible-to-the-user-as-separate-app*
  window — the child window tracks Tauri's window position.
- 🚫 Not literally in-process. The CEF helper binary is its own
  Mach task. The bundle ships an extra executable. (Already true
  for CEF helper processes regardless — Chromium is multi-process
  by design — so the marginal cost is the parent helper, not the
  full set.)
- 🚫 Cross-platform parenting story is not uniform. Windows has
  `SetParent` for child windows; Linux/X11 has `XReparentWindow`
  with caveats; Wayland forbids cross-process embedded toplevels
  (same retirement as the prior spike).
- 🚫 Native-view embedding into a Tauri-owned NSView (the literal
  ADR-0019 shape) is not delivered.

The hard rule's wording is *"the user is never **forced** to leave
the Thalyn app for any in-scope workflow."* A CEF child window
that tracks the parent's frame and Z-order is, from the user's
perspective, the Thalyn app. They do not alt-tab. They do not see
a separate dock entry. The hard rule's *spirit* is honored even
though the literal "single NSWindow.contentView hierarchy" is not.

This is Option D. It is the v0.29 path that delivers a usable
browser engine while honoring the hard rule, at the cost of
deferring the literal in-process embedding to a follow-on phase.

### F7. Option C (upstream Tao API) is right but slow.

A `tauri::Builder::with_application_class(...)` API would let
host apps inject their own NSApplication subclass. Filed against
tauri-apps/tao, this is a clean change with a defensible rationale
(*"some apps need NSApplication-protocol-conforming subclasses
that Tao does not know about"*). Landing it is multi-month.

Pursue in parallel as a long-term cleanup; do not block v0.29 on it.

## Recommendation

**Adopt Option D for v0.29.** Land the CEF child binary
(`thalyn-cef-host`), the parent-side supervisor (carries forward
the v1 sidecar's spawn/discovery/exit lifecycle, with the
*important* difference that the child binary is **our** bundled
Chromium rather than the user's installed Chrome), and the
macOS `NSWindow.addChildWindow` parenting on top.

What this means for the v0.29 phase scope:

- ADR-0019 stays *Accepted* but adds a refinement section
  documenting the Tao+CEF NSApplication conflict, the multi-week
  cost of literal in-process embedding, and the chosen v0.29
  shape (child binary + child window).
- The engine-swap *intent* (bundled Chromium, hard-rule compliant
  default flow, brain CDP unchanged, v1 system-Chromium discovery
  retired) ships in v0.29.
- The literal *in-process* embedding into the Tauri main process
  becomes a follow-on phase
  (`v0.30 — In-Process CEF Embedding (Tao integration)`),
  bumping the existing v0.30 (Multi-Project Juggling) one slot.
  The follow-on phase does the combined-NSApplication subclass +
  tao patch (or upstream API) work in isolation, with v0.29's
  child-window shape as the fallback that stays shipping if the
  follow-on hits a surprise.
- Linux X11/XWayland and Windows parenting paths land alongside
  macOS in v0.29. Wayland-native parenting stays on the
  going-public-checklist.

The plan revision (v0.29 reshape, v0.30 → v0.31 push, new v0.30
slot) is a user-facing change to the build sequence and warrants
human review before the next code commit lands.

## Risks not retired

- **Cross-process child window UX on Linux X11/XWayland.** The pattern
  (`XReparentWindow` between two app's toplevels) does work
  cross-process but is more fragile than macOS's `addChildWindow:`.
  Prototype in v0.29 implementation; if it doesn't fly, the Linux
  path falls back to the OSR variant for that platform alone.
- **Wayland.** As before, native cross-process embedded toplevels
  are forbidden. The going-public-checklist already has the
  CEF-native-Wayland row; this spike does not move it.
- **DRM and Chrome auto-update.** Bundled CEF doesn't auto-update;
  the dependency-review cadence carries the upgrade story (no
  change to that pre-existing finding).
- **In-process embedding (the eventual literal ADR-0019 shape).**
  Not retired. Becomes the new v0.30's load-bearing exit criterion.
- **Heuristic OAuth IdP detection of CEF.** Unchanged from the
  prior spike; CEF child binary doesn't change the UA fingerprint.

## Refinement (post-investigation)

After the report above was filed and the v0.29 phase started
landing the child-binary scaffolding, the macOS implementation
of step 7b surfaced a deeper limit that retires Option D:
**modern macOS does not support cross-process window hosting**.
F6's recommendation rested on the unverified assumption that
`[parent_window addChildWindow:child_window ordered:NSWindowAbove]`
works when `parent_window` and `child_window` belong to
different processes. It does not.

Evidence:

- **CEF maintainer** Marshall Greenblatt on the [CEF Forum](https://magpcss.org/ceforum/viewtopic.php?f=6&t=19593)
  ("[MacOS] embedding cefsimple browser into another window"):
  cross-process CEF embedding on macOS may be possible "but
  likely complicated"; the NSView/NSWindow handle-passing
  pattern that works on Linux/Windows does not work on macOS.
  Recommended fallbacks: consolidate to one process, IPC-driven
  event forwarding with rendering still in CEF's own window, or
  IOSurface-shared-memory rendering.
- **Chromium engineer** Nico Weber on [chromium-dev](https://groups.google.com/a/chromium.org/g/chromium-dev/c/H5fQcXllT3E)
  ("multi-process hosted browser window on Mac/Linux"):
  *"I don't think OS X supports cross-process window hosting."*
  Stuart Morgan adds that Apple's own Java Plugin2 attempted it
  and abandoned the approach.
- **Apple's `NSWindow.addChildWindow(_:ordered:)`** takes an
  `NSWindow*` instance. `NSWindow*` is owned by the calling
  process's `NSApp` window list — there is no public API that
  hands you an `NSWindow*` for a window owned by another
  process. The CGS / Quartz private APIs (`CGSConnection`,
  `CGSWindowID`, `CGSSetWindowParent`) exist but are
  Apple-private and not a path for shipping software.

This invalidates Option D's recommendation. Three credible
unblocks remain:

1. **Frame-tracking IPC fallback** (option 1 in the
   investigation write-up). Parent observes its NSWindow's
   move/resize/miniaturize/key notifications, forwards deltas
   over IPC; child applies via `setFrame:` /
   `orderOut:` / `orderFront:`. Combined with the child
   binary's activation policy set to `Accessory` /
   `LSUIElement = true`. ~2-3 days of work, but throwaway when
   in-process embedding lands.
2. **Pull the in-process embedding work forward** (option 2).
   Skip the child binary entirely; do the combined
   `ThalynApplication` NSApplication subclass + tao integration
   (runtime swizzle / vendored fork / upstream API). Multi-week,
   but the literal end-state ADR-0019 specified.
3. **OSR-only on macOS** (option 3). CEF renders to an
   IOSurface; parent composites. Cheap (~1 week) but loses
   F4.3 capabilities (passkeys, IME, DRM, drag-drop) — the
   exact reasons the 2026-04-27 spike retired OSR-as-primary.

**Decision: option 2.** v0.29 keeps its phase number for the
foundation work that landed (ADR ratification, lifecycle
scaffold, drawer surface, rect plumbing) and is reusable; v0.30
is reframed as the engine-swap-ship phase that lands the
in-process embedding. The phase split this report originally
recommended is unwound. ADR-0019's refinement section is
revised to reflect the single-phase shape; the build sequence
carries the new phase scopes.

The risks-not-retired list above stands except for "Cross-process
child window UX on Linux X11/XWayland" — Linux X11 *does*
support cross-process embedding via XEmbed, so v0.30's Linux
path can pursue it directly. The macOS portion of that risk
moves into v0.30's tao-integration risk row.
