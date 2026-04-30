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
//! `cef::initialize` is *not* called from this module today. The
//! follow-on commit that retires v0.29's `thalyn-cef-host`
//! `[[bin]]` and reshapes [`crate::cef::CefHost::start`] is where
//! the engine actually starts running in the parent process. This
//! module establishes the load-bearing scaffolding so that commit
//! is purely about engine semantics, not about wiring discipline.

#![cfg(feature = "cef")]

use std::sync::OnceLock;

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
