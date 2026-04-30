---
date: 2026-04-30
risk: ADR-0019's in-process CEF embedding requires the macOS helper-bundle layout (`<App>.app/Contents/Frameworks/Chromium Embedded Framework.framework/` plus four-or-five `Thalyn Helper*.app` bundles) to be present next to the parent exe, otherwise `cef::initialize` cannot spawn the renderer / GPU / utility / plugin / alerts subprocesses. v0.30's engine swap is gated on the helper-bundle layout being produced by the build pipeline.
adr: 0019, 0029
---

# Spike: cef-helper-bundle-integration

- **Question:** What is the right integration shape for producing
  the macOS CEF helper-bundle layout under
  `<App>.app/Contents/Frameworks/` as part of the existing Tauri 2
  bundle pipeline (`tauri build`)?
- **Time-box:** 1h. **Actual:** ~45m of code reading + Tauri schema
  inspection.
- **Outcome:** Partially answered — the available knobs are
  enumerated, the recommended shape is documented, but a real
  `tauri build` run is required before the recommendation is
  ratified into code. ADR-0029 still stands; this spike informs
  the integration commit.

## Approach

1. Read cef-rs's `build_util/mac.rs` (the public `bundle()` /
   `build_bundle()` functions and the supporting `BundleInfo` /
   `InfoPlist` shape) to understand what the helper-bundle layout
   requires structurally.
2. Read cef-rs's `bundle-cef-app` `[[bin]]` (the standalone CLI
   that drives `bundle()` from a Cargo workspace) to understand
   what that tool produces and how it is invoked.
3. Inspect Tauri 2's `config.schema.json` (shipped with
   `@tauri-apps/cli`) for bundle-pipeline hooks
   (`beforeBundleCommand`, `bundle.macOS.frameworks`,
   `bundle.macOS.files`).
4. Cross-reference the v0.29 child-binary path's helper-bundle
   commentary in
   `docs/spikes/2026-04-30-cef-macos-message-loop.md` and
   ADR-0029's helper-bundle section.
5. Run `cargo run --bin thalyn --features cef` from the dev tree
   (no helper-bundle layout) to confirm the swizzle's
   `ProtocolNotLinked("CefAppProtocol")` failure mode is what
   surfaces when the framework is absent — establishes the
   "before" state we expect this work to clear.

## Findings

### F1. cef-rs's `build_util` exposes `bundle()` / `build_bundle()` but they create a fresh `.app` from scratch.

`cef::build_util::mac::bundle()` is the public API for producing
a CEF-shaped macOS app. It takes an `app_path` and an
`executable_name` and emits `<app_path>/<executable_name>.app/`
with the full structure: the parent executable in
`Contents/MacOS/`, `Chromium Embedded Framework.framework`
copied into `Contents/Frameworks/`, and five helper `.app`
bundles (`Helper`, `Helper (GPU)`, `Helper (Renderer)`,
`Helper (Plugin)`, `Helper (Alerts)`) in
`Contents/Frameworks/`.

The function does not have a "merge into existing app" mode.
Calling it against a path that already contains a Tauri-built
`Thalyn.app` would either overwrite Tauri's main app (bad — loses
Tauri's `Info.plist`, frontend assets, codesigning) or refuse to
proceed.

`cef::build_util::mac` also does not export `create_app` (the
helper-bundle creation primitive) publicly. The `HELPERS` const
list and the `InfoPlist` struct are private.

### F2. cef-rs's `bundle-cef-app` `[[bin]]` requires Cargo metadata that Tauri does not provide.

The `bundle-cef-app` binary calls `build_bundle()`, which in turn
reads `cef::build_util::metadata::parse_bundle_metadata` from the
calling crate's `Cargo.toml`. The metadata format includes a
`helper_name` (the helper `[[bin]]` target name) and a
`resources_path`. Tauri's project layout does not produce or
consume this metadata, so `bundle-cef-app` can't be invoked
against the Thalyn workspace as-is.

It is reusable as a *library* (the public `bundle()` /
`build_bundle()`), but the binary itself targets cef-rs's own
example layout, not a Tauri layout.

### F3. Tauri 2's bundle pipeline has no `afterBundleCommand` hook.

`@tauri-apps/cli/config.schema.json` lists three pre-bundle
hooks:

- `build.beforeDevCommand` — fires before `tauri dev`.
- `build.beforeBuildCommand` — fires before `tauri build`'s
  cargo-build phase.
- `build.beforeBundleCommand` — fires after the cargo build
  but **before** the bundling phase.

There is no `afterBundleCommand`, no `afterBuildCommand`, and no
plugin hook for "modify the produced `.app`." Modifications must
either pre-stage content for the bundler to copy in, or wrap
`tauri build` from outside.

`bundle.macOS.frameworks` is documented as accepting `.framework`
paths only ("any macOS X frameworks that need to be bundled with
the application"). The schema does not promise it accepts `.app`
helper bundles.

`bundle.macOS.files` accepts a `{ "destination": "source" }`
map, which can copy individual files into the produced `.app`.
For directory copying (a helper `.app` is a directory tree), one
would have to enumerate every file — painful, fragile against
CEF SDK upgrades.

### F4. Three integration shapes are credible.

- **Option A — Manual post-build script.**
  Write `scripts/bundle-cef-helpers.sh` (or a small Rust
  binary) that takes a `.app` path and injects the framework +
  five helper `.app` bundles. Document a build flow:

  ```
  pnpm tauri build --features cef
  scripts/bundle-cef-helpers.sh \
      src-tauri/target/release/bundle/macos/Thalyn.app
  ```

  The script uses cef-rs's public `bundle()` API for the helper
  creation (or replicates the Info.plist generation directly via
  the `plist` crate). Codesigning, if added later, runs after
  this step.

  Pros: small commit, clear ownership, easy to iterate. Cons:
  manual second step that's easy to forget.

- **Option B — `beforeBundleCommand` + manual file declarations.**
  `beforeBundleCommand: "scripts/bundle-cef-helpers.sh
  --stage-into target/cef-stage"` runs before the bundler and
  produces a staging directory with the framework + helper
  bundles. Then `bundle.macOS.files` enumerates every file
  inside the helpers (each Info.plist, each executable copy)
  with destination paths under `Contents/Frameworks/`. Tauri's
  bundler picks them up.

  Pros: integrated into `tauri build`. Cons: enumerating every
  file inside the helper bundles in `tauri.conf.json` is
  painful and brittle — every CEF SDK bump that adds resources
  requires updating the manifest. Practically, the script that
  stages would also have to update the manifest, which means
  generated configuration, which means tauri.conf.json drifts
  away from being source-of-truth.

- **Option C — Wrapper script that calls `tauri build` then
  injects helpers.** A `scripts/build-bundled.sh` wraps the
  whole build:

  ```sh
  #!/bin/sh
  pnpm tauri build --features cef "$@"
  cargo run --features cef --bin bundle-cef-helpers -- \
      --app src-tauri/target/release/bundle/macos/Thalyn.app
  ```

  Document this as the canonical "produce a runnable bundled
  Thalyn" command. CI runs the wrapper, not `tauri build`
  directly.

  Pros: same one-command flow as Tauri's `tauri build`,
  manifest stays clean. Cons: doesn't compose with `tauri
  build --bundles dmg` cleanly (we'd need the wrapper to
  iterate per bundle target and inject pre-DMG).

### F5. Helper executable choices: copy vs. symlink vs. separate `[[bin]]`.

Each helper `.app` needs a `Contents/MacOS/<Helper Name>`
executable. Three options:

- **Copy parent binary.** Each helper bundle is a full copy of
  the Thalyn binary (~80 MB debug). Five helpers × parent size
  = ~400 MB. The helper subprocess re-execs through Thalyn's
  `main()` and `cef::execute_process` short-circuits. Works
  out of the box.
- **Symlink to parent binary.** Each helper bundle has a
  symlink to `../../MacOS/Thalyn`. Smaller bundle. Apple's
  codesigning in production requires real binaries (or
  re-signed copies); for v0.30 dev (codesigning is post-v1
  per the going-public-checklist) symlinks are fine.
- **Separate helper `[[bin]]` target.** A small Rust binary
  that just loads CEF and runs `cef::execute_process` — much
  smaller than the parent. Each helper bundle gets a copy of
  this small binary. Cleaner for codesigning isolation
  (helpers can have different entitlements). Adds a `[[bin]]`
  target to `src-tauri/Cargo.toml`.

For v0.30 dev, **symlinks** are the smallest move. Production
should switch to a separate helper `[[bin]]` once codesigning is
in scope.

## Recommendation

**Adopt Option A (manual post-build script) for the v0.30 commit
that lands helper-bundle integration.** Specifically:

1. New `[[bin]]` target `bundle-cef-helpers` in
   `src-tauri/Cargo.toml`, gated on `feature = "cef"` and
   `target_os = "macos"`. The binary takes
   `--app <path>.app --cef-sdk <path>` arguments.
2. The binary:
   - Copies `<cef-sdk>/Chromium Embedded Framework.framework/`
     into `<app>/Contents/Frameworks/`.
   - Creates the five helper `.app` bundles in
     `<app>/Contents/Frameworks/` per the cef-rs `HELPERS` list,
     each with an Info.plist that matches cefsimple's shape
     (`LSUIElement = 1`, `CFBundleIdentifier`,
     `CFBundleExecutable`, etc.).
   - Symlinks the parent Thalyn binary into each helper's
     `Contents/MacOS/`.
3. `CONTRIBUTING.md` gains a section documenting the build
   flow: `pnpm tauri build --features cef` followed by
   `cargo run --features cef --bin bundle-cef-helpers --
   --app .../Thalyn.app`.
4. The follow-on engine-swap commit (calls `cef::initialize`
   from the setup hook + reshapes `CefHost::start`) lands on
   top of this; verifying it requires running the produced
   bundle.

Skip Option B (manifest enumeration is too brittle). Defer
Option C (wrapper script) until the `tauri build` flow is
otherwise stable — wrapping is easy to add later without
re-architecting.

For helper executables, use **symlinks** for v0.30. Add a row to
`docs/going-public-checklist.md` saying "switch helper bundles
from symlinks to copies (or a separate helper `[[bin]]` target)
when codesigning lands."

## Risks not retired

- **Tauri's `bundle.macOS.frameworks` accepting `.app`
  helpers.** Schema description says ".framework" only, but the
  underlying tauri-bundler crate may accept `.app`. Worth a 10-min
  test on a real `tauri build` before locking Option A in. If it
  works, Option B becomes more attractive.
- **DMG / NSIS / RPM bundle targets.** Option A only addresses
  the macOS `.app`. Windows / Linux equivalent helper-bundle
  shapes are different (Windows: `<app-dir>/Thalyn Helper.exe`
  files, no Info.plist; Linux: similar flat layout). The
  cross-platform parity is in v0.30's scope but not addressed by
  this spike.
- **Codesigning.** Helper bundles must be signed with the same
  identity as the parent app, or the runtime aborts. Going-public
  checklist already tracks this; v0.30 ships unsigned for
  development use.
- **Resources / `.lproj` localisation.** cef-rs's `bundle()`
  copies resources from a configurable `resources_path`. Whether
  Thalyn needs CEF's own resource bundle (locales,
  icudtl.dat) re-copied is part of the framework copy — should
  Just Work but worth confirming via a real run.

## Refinement (post-investigation)

After this spike was filed, a follow-up audit of `tauri-bundler`'s
macOS source (`crates/tauri-bundler/src/bundle/macos/app.rs` on
the `dev` branch) and a survey of community CEF-on-Tauri-2 work
overturned the spike's original recommendation in two ways.

**1. `bundle.macOS.frameworks` rejects `.app` paths.**
`copy_frameworks_to_bundle` validates extensions with
`ends_with(".framework")` and `ends_with(".dylib")`; a
`.app` path errors with "Framework path should have .framework
extension". So the spike's Option A reading ("frameworks accepts
helper apps") was wrong; the framework field is for the
Chromium framework only.

**2. `bundle.macOS.files` accepts directory entries.**
`copy_custom_files_to_bundle` takes `HashMap<dest, source>` and
recursively copies both files *and directories*. A single entry
like `"Frameworks/Thalyn Helper.app": "target/cef-helpers/Thalyn
Helper.app"` copies the entire helper bundle directory tree. We
do not need to enumerate every file inside — the spike's
"painful manifest enumeration" objection to Option B was based
on misreading the API.

**Updated recommendation: Option B-as-corrected.** Land a
`beforeBundleCommand` script that stages the framework + the
five helper `.app` bundles into `src-tauri/target/cef-helpers/`,
then declare them in `tauri.conf.json`:

```json
"bundle": {
  "macOS": {
    "frameworks": [
      "target/cef-helpers/Chromium Embedded Framework.framework"
    ],
    "files": {
      "Frameworks/Thalyn Helper.app":
        "target/cef-helpers/Thalyn Helper.app",
      "Frameworks/Thalyn Helper (GPU).app":
        "target/cef-helpers/Thalyn Helper (GPU).app",
      "Frameworks/Thalyn Helper (Renderer).app":
        "target/cef-helpers/Thalyn Helper (Renderer).app",
      "Frameworks/Thalyn Helper (Plugin).app":
        "target/cef-helpers/Thalyn Helper (Plugin).app",
      "Frameworks/Thalyn Helper (Alerts).app":
        "target/cef-helpers/Thalyn Helper (Alerts).app"
    }
  }
}
```

`pnpm tauri build --features cef` becomes the single command
that produces a complete bundled `.app` with helpers in place;
no manual second step.

**Helper executable: separate `[[bin]]`, not symlinks.** The
spike's symlink recommendation was scoped to "v0.30 dev" with a
codesigning rotation in front of it. The audit changed that
calculus: a separate `thalyn-cef-helper` `[[bin]]` target —
~15 lines of Rust that calls `LibraryLoader::new(.., helper =
true)` and `cef::execute_process` — is small enough that
landing it now beats deferring. Production-ready shape from the
start; codesigning isolation works without a follow-on rotation;
helper bundles are tiny (~few-MB helper binary instead of
copies/symlinks of the ~80MB parent). The v0.29 `thalyn-cef-host`
`[[bin]]` was a different shape (full Chromium engine in a child
process); the helper `[[bin]]` is a pure subprocess entry point.

**Caveats not retired** by the audit:

- **Codesigning ordering.** When signing lands (post-v1),
  helpers must be signed before the outer `.app`,
  deepest-first. tauri-bundler signs `.framework` entries from
  the `frameworks` field but its behaviour on `.app` directories
  copied via `files` needs a real verification run. May need a
  pre-signing step in the `beforeBundleCommand` or a manual
  signing pass before notarisation.
- **Cross-platform parity.** Windows / Linux helper layouts are
  different (Windows has flat `Helper.exe` files; Linux is
  similar). The `bundle.macOS.files` shape is macOS-only; the
  Windows / Linux paths follow the same staging pattern but
  with platform-specific destinations. v0.30 lands macOS first;
  Windows / Linux follow.
- **Resources copy.** CEF's `Resources/` directory inside the
  framework (locales, icudtl.dat) is part of the framework
  itself; the recursive copy of
  `Chromium Embedded Framework.framework/` brings them along.
  Confirmed on inspection but worth eyeballing on the first
  real `tauri build` run.

The **community-status** of CEF-on-Tauri-2 integration is "no
prior art." cef-rs ships `bundle-cef-app` for standalone CEF
apps; nothing documents the cef-rs + Tauri 2 combination. We
are setting the pattern.
