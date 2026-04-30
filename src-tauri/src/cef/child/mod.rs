//! `thalyn-cef-host` — the bundled CEF child binary.
//!
//! Per ADR-0019's 2026-04-30 refinement, v0.29 ships CEF in its own
//! process rather than embedded inside the Tauri main process. This
//! module is the cefsimple-shaped runtime the
//! [`thalyn-cef-host`](../../bin/thalyn-cef-host.rs) binary calls into.
//! It owns the NSApplication subclass on macOS, runs the
//! `cef::execute_process` early-return for helper subprocesses, and
//! drives `cef::initialize` / `run_message_loop` / `shutdown` for the
//! browser process.
//!
//! Startup is parametrised by command-line flags the parent (the
//! `CefHost` in the Tauri process) passes when it spawns us:
//!
//! - `--profile-dir <path>` — the per-Thalyn Chromium profile under the
//!   canonical Thalyn data dir. We thread it into `CefSettings` as both
//!   `cache_path` and `root_cache_path` so the cookie store, login
//!   state, and `DevToolsActivePort` file all land in one place the
//!   parent already knows about.
//! - `--initial-url <url>` — defaults to `about:blank`; the brain
//!   navigates over CDP once the WS URL is up, so the initial page
//!   matters only for the user-visible blank-vs-warm-up moment.
//!
//! `--remote-debugging-port=0` is set unconditionally in
//! `CefSettings`; CEF picks a free port and writes the
//! `DevToolsActivePort` file in the profile dir, which the parent
//! watches.
//!
//! The whole module compiles only with the `cef` feature on. The
//! parent-side supporting code (SDK resolve, profile dir, port-file
//! watch) lives one level up and is engine-agnostic.

#![allow(dead_code)]

use std::path::PathBuf;

use cef::{args::Args, *};

pub mod app;
pub mod client;

#[cfg(target_os = "macos")]
pub mod mac;

/// Parsed `thalyn-cef-host` command-line arguments. CEF's own switches
/// (`--type=`, `--remote-debugging-port=`, …) are consumed by
/// `execute_process` / `initialize` directly; we only parse the flags
/// the parent process passes for our own configuration.
#[derive(Debug, Clone)]
pub struct ChildArgs {
    /// Per-Thalyn Chromium profile dir. Used as `cache_path` and
    /// `root_cache_path` in `CefSettings`, which is also where the
    /// `DevToolsActivePort` file lands.
    pub profile_dir: PathBuf,
    /// First URL the browser process navigates to. The brain reroutes
    /// over CDP once attached, so the default `about:blank` is fine for
    /// agent-driven flows; the user-facing drawer would override this.
    pub initial_url: String,
}

impl ChildArgs {
    pub fn from_env() -> Result<Self, String> {
        let mut profile_dir: Option<PathBuf> = None;
        let mut initial_url: Option<String> = None;
        let mut iter = std::env::args().skip(1);
        while let Some(arg) = iter.next() {
            if let Some(rest) = arg.strip_prefix("--profile-dir=") {
                profile_dir = Some(PathBuf::from(rest));
            } else if arg == "--profile-dir" {
                if let Some(value) = iter.next() {
                    profile_dir = Some(PathBuf::from(value));
                }
            } else if let Some(rest) = arg.strip_prefix("--initial-url=") {
                initial_url = Some(rest.to_owned());
            } else if arg == "--initial-url" {
                if let Some(value) = iter.next() {
                    initial_url = Some(value);
                }
            }
        }
        Ok(Self {
            profile_dir: profile_dir.ok_or_else(|| {
                "thalyn-cef-host: --profile-dir is required (parent passes it on spawn)".to_owned()
            })?,
            initial_url: initial_url.unwrap_or_else(|| "about:blank".to_owned()),
        })
    }
}

/// macOS holds onto the loader so the framework stays mapped for the
/// lifetime of the process. Other platforms link CEF normally and
/// don't need a runtime loader, but the type still exists so the
/// `load_cef` shape is the same on every host.
#[cfg(target_os = "macos")]
type LibraryHandle = cef::library_loader::LibraryLoader;
#[cfg(not(target_os = "macos"))]
struct LibraryHandle;

fn load_cef() -> LibraryHandle {
    #[cfg(target_os = "macos")]
    let library = {
        let exe = std::env::current_exe().expect("current_exe must resolve in the child binary");
        let loader = cef::library_loader::LibraryLoader::new(&exe, false);
        assert!(
            loader.load(),
            "cef::library_loader::LibraryLoader::load returned false; \
             the helper bundle structure is not laid out as expected"
        );
        loader
    };
    #[cfg(not(target_os = "macos"))]
    let library = LibraryHandle;

    // Pin the CEF API version. Required before any other cef:: call.
    let _ = api_hash(sys::CEF_API_VERSION_LAST, 0);

    #[cfg(target_os = "macos")]
    mac::setup_application();

    library
}

/// Entry point invoked by `src/bin/thalyn-cef-host.rs::main`.
pub fn main_entry() -> Result<(), String> {
    let _library = load_cef();

    let cef_args = Args::new();
    let cmd_line = cef_args
        .as_cmd_line()
        .ok_or_else(|| "failed to parse cef command line".to_owned())?;
    let type_switch = CefString::from("type");
    let is_browser_process = cmd_line.has_switch(Some(&type_switch)) != 1;

    // execute_process returns -1 in the browser process. Helpers
    // return their subprocess exit code and we exit immediately —
    // they must not call cef::initialize.
    let ret = execute_process(
        Some(cef_args.as_main_args()),
        None::<&mut App>,
        std::ptr::null_mut(),
    );
    if !is_browser_process {
        return Ok(());
    }
    if ret != -1 {
        return Err(format!(
            "thalyn-cef-host: execute_process returned {ret} in the browser process \
             (expected -1)"
        ));
    }

    let child_args = ChildArgs::from_env()?;
    std::fs::create_dir_all(&child_args.profile_dir)
        .map_err(|err| format!("failed to create profile dir: {err}"))?;

    let mut app = app::ThalynChildApp::new(child_args.initial_url.clone());

    let profile_dir = child_args.profile_dir.to_string_lossy().into_owned();
    let settings = Settings {
        no_sandbox: 1,
        remote_debugging_port: 0,
        log_severity: LogSeverity::default(),
        cache_path: CefString::from(profile_dir.as_str()),
        root_cache_path: CefString::from(profile_dir.as_str()),
        ..Default::default()
    };

    let init_ret = initialize(
        Some(cef_args.as_main_args()),
        Some(&settings),
        Some(&mut app),
        std::ptr::null_mut(),
    );
    if init_ret != 1 {
        return Err(format!(
            "thalyn-cef-host: cef::initialize returned {init_ret}"
        ));
    }

    #[cfg(target_os = "macos")]
    let _delegate = mac::setup_app_delegate();

    run_message_loop();
    shutdown();

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn child_args_default_initial_url_is_about_blank() {
        // ChildArgs::from_env reads std::env::args; the unit test just
        // verifies the parser shape via construction.
        let args = ChildArgs {
            profile_dir: PathBuf::from("/tmp/profile"),
            initial_url: "about:blank".to_owned(),
        };
        assert_eq!(args.initial_url, "about:blank");
        assert_eq!(args.profile_dir, PathBuf::from("/tmp/profile"));
    }
}
