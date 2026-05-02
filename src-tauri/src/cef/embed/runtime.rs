//! Pre-`tauri::Builder` and Tauri-setup-hook helpers for the
//! in-process CEF engine.
//!
//! Two entry points:
//!
//! - [`run_pre_tauri_setup`] is called from `main()` before
//!   `thalyn_lib::run()` starts Tauri. It maps the CEF framework
//!   on macOS (so the framework's symbols and Objective-C
//!   protocol entries become available to the rest of the
//!   process) and runs `cef::execute_process` to short-circuit
//!   helper subprocesses (renderer, gpu-process, utility) — those
//!   re-execute the Thalyn binary with a `--type=` switch and
//!   exit through this path without ever reaching Tauri.
//! - [`install_swizzle_inside_setup_hook`] is called from inside
//!   Tauri's `setup` callback. By that point tao has registered
//!   `TaoApp` as an `NSApplication` subclass and locked it in as
//!   the principal class, but the run loop has not yet spun. The
//!   swizzle adds CEF's protocol contracts to `TaoApp` — see
//!   [`super::tao_app`] for the runtime-Objective-C surface.
//!
//! [`initialize_cef_engine`] is the load-bearing one-shot:
//! resolves the SDK, opens the per-Thalyn profile, runs the swizzle,
//! and calls `cef::initialize` against the active `NSApp`. After
//! this returns successfully, the CEF runtime is process-global
//! and the brain attaches over CDP via the WS URL surfaced by
//! [`crate::cef::CefHost`].

#![cfg(feature = "cef")]

use std::path::Path;
use std::sync::OnceLock;

use crate::cef::profile::{CefProfile, ProfileError};

/// Holds the CEF library loader for the lifetime of the process so
/// the framework stays mapped. Dropping the loader unmaps the
/// framework, which would invalidate every CEF symbol. macOS only —
/// Linux and Windows link CEF at compile time, so they do not need
/// a runtime loader.
#[cfg(target_os = "macos")]
static LIBRARY_LOADER: OnceLock<cef::library_loader::LibraryLoader> = OnceLock::new();

/// Pre-`tauri::Builder` browser-process setup. Called from `main()`
/// before `thalyn_lib::run()`.
///
/// Returns:
///
/// - `Some(code)` if this invocation is a CEF helper subprocess
///   (`cef::execute_process` ran the helper's work and produced an
///   exit code). The caller must `std::process::exit(code)`
///   immediately — helpers must NOT continue into Tauri.
/// - `None` if this is the browser process (or if CEF is not
///   available at this exe's bundle location, e.g. an unbundled
///   dev run). Caller continues into `thalyn_lib::run()`.
pub fn run_pre_tauri_setup() -> Option<i32> {
    #[cfg(target_os = "macos")]
    {
        if !load_cef_framework_macos() {
            // Unbundled dev run — the helper-bundle layout under
            // `<App>.app/Contents/Frameworks/` does not exist next
            // to this exe. Skip the rest of the CEF browser-process
            // setup; CefHost will surface a "CEF not loaded" state
            // if anyone tries to start the engine.
            return None;
        }
    }
    run_execute_process()
}

/// Setup-hook helper: install the `ThalynApplication` swizzle on
/// `TaoApp`. Must be called from inside Tauri's `setup` callback —
/// after the Tauri runtime has built its EventLoop (so tao has
/// registered `TaoApp`) but before the run loop spins.
///
/// On non-macOS targets this is a no-op (Linux and Windows do not
/// have the NSApplication-subclass conflict the swizzle is
/// resolving).
///
/// Errors are logged but do not abort. The two recoverable cases
/// are an unbundled dev run (`ProtocolNotLinked` because the CEF
/// framework's protocol entries are unavailable) and a
/// double-install (`Ok(())` returned via the idempotency guard
/// inside the swizzle helper). A `TaoAppNotFound` would indicate
/// something fundamentally wrong with the Tauri/tao link and is
/// logged as an error so a human notices.
pub fn install_swizzle_inside_setup_hook() {
    #[cfg(target_os = "macos")]
    {
        match super::tao_app::install_thalyn_application_swizzle() {
            Ok(()) => {
                tracing::debug!(
                    target = "thalyn::cef",
                    "ThalynApplication swizzle installed"
                );
            }
            Err(err) => {
                tracing::error!(
                    target = "thalyn::cef",
                    ?err,
                    "ThalynApplication swizzle failed; CEF will crash on the first event \
                     if the engine is started without the protocols on TaoApp"
                );
            }
        }
    }
}

#[cfg(target_os = "macos")]
fn load_cef_framework_macos() -> bool {
    if LIBRARY_LOADER.get().is_some() {
        return true;
    }
    let Ok(exe) = std::env::current_exe() else {
        return false;
    };
    let Some(parent) = exe.parent() else {
        return false;
    };
    // The helper-bundle layout puts the framework at
    // `<App>.app/Contents/Frameworks/` and the parent exe at
    // `<App>.app/Contents/MacOS/`. cef-rs's `LibraryLoader::new`
    // canonicalises this path internally and panics on missing —
    // so we sniff for the framework first and bail without
    // calling `LibraryLoader::new` at all when it's absent.
    let framework_marker = parent
        .join("../Frameworks/Chromium Embedded Framework.framework/Chromium Embedded Framework");
    if !framework_marker.exists() {
        return false;
    }
    let loader = cef::library_loader::LibraryLoader::new(&exe, false);
    if !loader.load() {
        return false;
    }
    // Negotiate the API version with the freshly loaded framework.
    // CEF's wrapper structs carry an inline `size = sizeof(_cef_*_t)`
    // header that the framework cross-checks against the version it
    // was compiled with; the cross-check returns `-1` ("invalid
    // version") until `cef_api_hash` runs at least once. Without
    // this, `cef::initialize` accepts an `App` pointer but the
    // first internal call into it aborts with
    // `CefApp_0_CToCpp called with invalid version -1`.
    let _ = cef::api_hash(cef::sys::CEF_API_VERSION_LAST, 0);
    let _ = LIBRARY_LOADER.set(loader);
    true
}

fn run_execute_process() -> Option<i32> {
    let cef_args = cef::args::Args::new();
    let exit_code = cef::execute_process(
        Some(cef_args.as_main_args()),
        None::<&mut cef::App>,
        std::ptr::null_mut(),
    );
    // -1 == browser process; helpers return their own exit code.
    if exit_code >= 0 {
        Some(exit_code)
    } else {
        None
    }
}

/// Errors returned by [`initialize_cef_engine`]. Each variant maps
/// to a discrete failure mode that callers can recover from
/// differently — most paths just log and continue without an
/// engine, so the renderer's browser drawer surfaces an "engine
/// not available" state instead of crashing.
#[derive(Debug, thiserror::Error)]
pub enum InitializeError {
    #[error(
        "CEF framework is not loaded; the helper-bundle layout under \
         `<App>.app/Contents/Frameworks/` was not present next to the \
         parent exe. Run `pnpm tauri build --features cef` to produce \
         a complete bundled .app, or set the layout up manually for an \
         unbundled dev run."
    )]
    FrameworkNotLoaded,
    #[cfg(target_os = "macos")]
    #[error("ThalynApplication swizzle failed: {0}")]
    Swizzle(#[from] super::tao_app::SwizzleError),
    #[error("could not open the per-Thalyn CEF profile: {0}")]
    Profile(#[from] ProfileError),
    #[error("cef::initialize returned {0} (expected 1)")]
    CefInitializeFailed(i32),
}

/// Holds the live `CefProfile` so the OS-level handles
/// `cef::initialize` opens against it stay valid for the process
/// lifetime. Dropping a `CefProfile` is harmless today (it's a
/// `PathBuf` wrapper), but parking it in a static keeps the
/// invariant explicit if profile internals grow.
static ACTIVE_PROFILE: OnceLock<CefProfile> = OnceLock::new();

/// Run `cef::initialize` against the per-Thalyn profile in
/// `profile_root`. Idempotent: a second call after success returns
/// `Ok(())` without re-initializing (CEF is process-global; multiple
/// init calls are an error).
///
/// Must be called on the main thread, from inside Tauri's setup
/// callback, *after* [`install_swizzle_inside_setup_hook`] has
/// installed the protocol contracts on `TaoApp`. The
/// `cef::execute_process` short-circuit in [`run_pre_tauri_setup`]
/// must already have gated out helper-process invocations of the
/// parent binary.
///
/// On macOS, `LIBRARY_LOADER` must already be populated by
/// [`run_pre_tauri_setup`] (which only happens when the helper
/// bundle layout is present); without that, `cef::initialize`
/// would crash trying to reach symbols from the framework. We
/// fail fast with [`InitializeError::FrameworkNotLoaded`] in that
/// case so the renderer can surface an engine-unavailable state
/// instead of the app dying on startup.
pub fn initialize_cef_engine(profile_root: &Path) -> Result<(), InitializeError> {
    if ACTIVE_PROFILE.get().is_some() {
        // Engine already initialised in this process. CEF rejects
        // double-init; nothing useful to do but bail cleanly.
        return Ok(());
    }

    #[cfg(target_os = "macos")]
    if LIBRARY_LOADER.get().is_none() {
        return Err(InitializeError::FrameworkNotLoaded);
    }

    #[cfg(target_os = "macos")]
    super::tao_app::install_thalyn_application_swizzle()?;

    let profile = CefProfile::open(profile_root)?;
    profile.clear_stale_port_file()?;

    let profile_dir_str = profile.dir().to_string_lossy().into_owned();
    // DevTools server is enabled via the
    // `--remote-debugging-port=0` switch injected from
    // `ThalynApp::on_before_command_line_processing`. The
    // `cef_settings_t::remote_debugging_port` field requires
    // 1024-65535 and disables the server otherwise — useless for
    // ephemeral-port mode, which is the contract the brain's CDP
    // path expects.
    let settings = cef::Settings {
        no_sandbox: 1,
        cache_path: cef::CefString::from(profile_dir_str.as_str()),
        root_cache_path: cef::CefString::from(profile_dir_str.as_str()),
        ..Default::default()
    };

    // Construct the App + BrowserProcessHandler so CEF has somewhere
    // to call back when the context is up. The handler reads the
    // host-view pointer the setup hook installed and parents the
    // user-facing Browser to it.
    let mut app = super::app::ThalynApp::build();

    let cef_args = cef::args::Args::new();
    let init_ret = cef::initialize(
        Some(cef_args.as_main_args()),
        Some(&settings),
        Some(&mut app),
        std::ptr::null_mut(),
    );
    if init_ret != 1 {
        return Err(InitializeError::CefInitializeFailed(init_ret));
    }

    let _ = ACTIVE_PROFILE.set(profile);
    tracing::info!(
        target = "thalyn::cef",
        profile_dir = %profile_dir_str,
        "CEF engine initialised in-process"
    );
    Ok(())
}

/// Whether the engine has been initialised in this process.
pub fn is_engine_initialized() -> bool {
    ACTIVE_PROFILE.get().is_some()
}

/// Path of the active profile, if [`initialize_cef_engine`] has
/// run successfully. Used by `CefHost` to read
/// `DevToolsActivePort` and surface the WS URL.
pub fn active_profile_dir() -> Option<&'static Path> {
    ACTIVE_PROFILE.get().map(|p| p.dir())
}
