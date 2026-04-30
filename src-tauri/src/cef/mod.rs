//! Bundled-Chromium engine (ADR-0019, refined by ADR-0029).
//!
//! Owns the CEF lifecycle inside the Rust core: SDK resolution
//! (where on disk the binary distribution lives), per-Thalyn
//! Chromium profile, the `DevToolsActivePort` watcher that produces
//! the WS URL the brain attaches to over CDP, and the [`CefHost`]
//! state machine that wraps it all.
//!
//! Engine model (v0.30):
//!
//! - `main()` → [`embed::runtime::run_pre_tauri_setup`] maps the
//!   framework on macOS and short-circuits CEF helper subprocesses.
//! - Tauri setup hook → [`embed::runtime::install_swizzle_inside_setup_hook`]
//!   grafts CEF's NSApplication-protocol contracts onto tao's
//!   `TaoApp` class, then [`embed::runtime::initialize_cef_engine`]
//!   calls `cef::initialize` against the per-Thalyn profile.
//! - `init_app_state` constructs [`CefHost`] and spawns an async
//!   task that calls [`CefHost::attach_to_active_engine`] to read
//!   `DevToolsActivePort` and surface the WS URL.
//!
//! Module gating: the cef-rs crate itself sits behind the `cef`
//! Cargo feature, so any code that calls into `cef::initialize` /
//! `cef::Browser` lives under `#[cfg(feature = "cef")]`. The
//! supporting infrastructure here (profile, port-file watcher,
//! `CefHost` state machine) is engine-agnostic and compiles in
//! every configuration.

#![allow(dead_code, unused_imports)]

pub mod embed;
pub mod host;
pub mod port_file;
pub mod profile;
pub mod sdk;

pub use host::{CefHost, CefSession, HostError, HostState, HostWindowRect};
pub use port_file::{parse_port_file, wait_for_port_file, DevToolsEndpoint, PortFileError};
pub use profile::{CefProfile, ProfileError};
pub use sdk::{pinned_cef_version, CefSdk, SdkResolveError};
