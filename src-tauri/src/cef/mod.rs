//! Bundled-Chromium engine (ADR-0019).
//!
//! Owns the CEF lifecycle inside the Rust core: SDK resolution
//! (where on disk the binary distribution lives), per-Thalyn
//! Chromium profile, the `DevToolsActivePort` watcher that produces
//! the WS URL the brain attaches to over CDP, and the [`CefHost`]
//! state machine that wraps it all.
//!
//! This module is the v2 replacement for the v1 sidecar Chromium
//! supervisor (`crate::browser`). The v1 path stays in tree until
//! the renderer drawer is wired through and the brain has been
//! migrated to the in-process WS URL — see `` §19.
//!
//! Module gating: the cef-rs crate itself sits behind the `cef`
//! Cargo feature, so any code that calls into `cef::initialize` /
//! `cef::Browser` lives under `#[cfg(feature = "cef")]`. The
//! supporting infrastructure here (SDK resolve, profile, port-file
//! watcher) is engine-agnostic and compiles in every configuration
//! — that lets the brain CDP path migrate ahead of the engine init.

#![allow(dead_code, unused_imports)]

#[cfg(feature = "cef")]
pub mod child;
pub mod embed;
pub mod host;
pub mod port_file;
pub mod profile;
pub mod sdk;

pub use host::{CefHost, CefSession, HostError, HostState, HostWindowRect};
pub use port_file::{parse_port_file, wait_for_port_file, DevToolsEndpoint, PortFileError};
pub use profile::{CefProfile, ProfileError};
pub use sdk::{pinned_cef_version, CefSdk, SdkResolveError};
