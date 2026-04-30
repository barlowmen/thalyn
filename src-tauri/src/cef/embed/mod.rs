//! In-process CEF embedding (ADR-0029).
//!
//! v0.29 ran CEF in a child binary parented to the Tauri main
//! window via `NSWindow.addChildWindow:`. That path closed when the
//! macOS investigation showed cross-process window hosting is not
//! supported on modern macOS (see
//! `docs/spikes/2026-04-30-cef-macos-message-loop.md`'s refinement
//! section). v0.30 collapses the two-process arrangement into a
//! single Tauri main process that hosts CEF in-process.
//!
//! This module is the substrate for that work. It owns the
//! Objective-C runtime swizzle that adds CEF's `CefAppProtocol` /
//! `CrAppProtocol` / `CrAppControlProtocol` contracts to tao's
//! `TaoApp` `NSApplication` subclass. The swizzle is the
//! load-bearing piece — without it CEF crashes with
//! `Check failed: nesting_level_ != 0` on the first event — and is
//! the path-choice ratified in ADR-0029.
//!
//! The swizzle helper is gated on `feature = "cef"` and
//! `target_os = "macos"`. It is dead code until the Tauri setup
//! hook (lands in a follow-on commit) calls it before
//! `cef::initialize`. Adding it here as an isolated, reviewable
//! piece keeps the runtime-Objective-C surface separable from the
//! init-sequence wiring it eventually pairs with.
//!
//! v1's system-Chromium sidecar code (`crate::browser`) and v0.29's
//! `thalyn-cef-host` `[[bin]]` retire in subsequent commits, once
//! the in-process path is wired and verifiable.

#![allow(dead_code)]

#[cfg(all(feature = "cef", target_os = "macos"))]
pub mod tao_app;
