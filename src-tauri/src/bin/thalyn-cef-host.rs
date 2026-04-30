//! `thalyn-cef-host` — the bundled CEF child binary parented to the
//! Tauri main window.
//!
//! Per ADR-0019's 2026-04-30 refinement (and
//! `docs/spikes/2026-04-30-cef-macos-message-loop.md`), the in-process
//! shape — single Tauri main process owning both windowing and CEF —
//! requires a combined `NSApplication` subclass + tao integration that
//! is multi-week work. v0.29 ships CEF in this child binary while
//! v0.30 lands the literal in-process embedding without scope
//! pressure.
//!
//! This file is intentionally a thin entry point: the runtime lives
//! in [`thalyn_lib::cef::child`] so the cefsimple-shape modules can
//! be unit-tested and re-used by the v0.30 in-process integration.
//!
//! Required-feature `cef` is enforced from the Cargo manifest, so
//! default trunk builds skip this binary entirely.

#![cfg(feature = "cef")]

fn main() {
    if let Err(err) = thalyn_lib::cef::child::main_entry() {
        eprintln!("thalyn-cef-host: {err}");
        std::process::exit(1);
    }
}
