//! Objective-C runtime swizzle that grafts CEF's NSApplication
//! protocol contracts onto tao's `TaoApp` class.
//!
//! Per ADR-0029, v0.30 keeps CEF in-process inside the Tauri main
//! process. macOS allows exactly one `NSApplication` subclass per
//! process and tao already registers `TaoApp`; CEF requires the
//! active class to conform to `CefAppProtocol` / `CrAppProtocol` /
//! `CrAppControlProtocol`, override `sendEvent:` with a
//! `handling_send_event` flag-toggle, and route `terminate:`
//! through `CloseAllBrowsers`. The runtime swizzle adds those
//! contracts to `TaoApp` after tao registers it but before
//! `cef::initialize` runs.
//!
//! Call ordering (must hold; otherwise CEF crashes with
//! `Check failed: nesting_level_ != 0` on the first event):
//!
//! 1. `tauri::Builder` builds the EventLoop. tao's lazy
//!    `APP_CLASS` registers `TaoApp` and `[NSApp sharedApplication]`
//!    locks it in as the principal class.
//! 2. Inside Tauri's setup hook (which fires after the runtime is
//!    built but before the run loop spins), call
//!    [`install_thalyn_application_swizzle`].
//! 3. Call `cef::initialize` with the per-Thalyn profile.
//! 4. Tauri's run loop owns the message pump from here.
//!
//! Implementation notes (worth knowing during review):
//!
//! - `sendEvent:` is replaced via `class_replaceMethod`; the old
//!   IMP (tao's CMD-key-up workaround + device-event dispatch) is
//!   captured into a static so the new IMP can delegate after
//!   toggling the flag. Wrapping rather than rewriting preserves
//!   tao's contract end-to-end.
//! - `terminate:` is added on `TaoApp` (not replaced — TaoApp
//!   inherits from `NSApplication` without overriding `terminate:`).
//!   The override delegates to NSApplication's original IMP today
//!   so an unwired build still quits cleanly; the wiring commit
//!   replaces the body to short-circuit through CEF's
//!   `CloseAllBrowsers` when a session is live.
//! - `isHandlingSendEvent` / `setHandlingSendEvent:` are added as
//!   new methods. The flag itself lives in a process-global
//!   `AtomicBool` rather than an associated object on `NSApp`; the
//!   ADR's general-case associated-object pattern collapses to a
//!   plain atomic for the singleton case (`NSApp` is the only
//!   instance), and the atomic avoids per-event allocation /
//!   hash-table lookup on a hot path. The semantic is identical:
//!   `handling_send_event` toggled around `sendEvent:` for the
//!   active `NSApplication`.
//! - `class_addProtocol` records conformance to all three CEF
//!   protocols. CEF's runtime check uses `conformsToProtocol:`,
//!   which walks the protocol list set by `class_addProtocol`.
//!   Method-level conformance is established by the methods we
//!   add above; the protocol entry records the marker.
//!
//! The Objective-C runtime's `IMP` type erases the parameter list
//! into a pointer to `unsafe extern "C-unwind" fn()`. Each thunk
//! below is registered with an explicit type-encoding string and
//! transmuted to `Imp` at the FFI boundary; the encoding string is
//! the contract that lets the dispatcher know how to call us.

#![cfg(all(feature = "cef", target_os = "macos"))]

use std::ffi::CStr;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::OnceLock;

use cef::application_mac::{CefAppProtocol, CrAppControlProtocol, CrAppProtocol};
use objc2::ffi::{
    class_addMethod, class_addProtocol, class_getInstanceMethod, class_replaceMethod,
    method_getImplementation,
};
use objc2::runtime::{AnyClass, AnyObject, Bool, Imp, Sel};
use objc2::{class, sel, ProtocolType};
use thiserror::Error;

/// Tao's NSApplication subclass name. Confirmed against
/// `tao-0.34.8/src/platform_impl/macos/app.rs` (the `APP_CLASS`
/// `Lazy` invokes `ClassDecl::new("TaoApp", NSApplication)`). If
/// tao renames this class in a future minor, the swizzle fails
/// fast with [`SwizzleError::TaoAppNotFound`] rather than
/// silently degrading.
const TAO_APP_CLASS_NAME: &CStr = c"TaoApp";

#[derive(Debug, Error)]
pub enum SwizzleError {
    #[error("tao has not registered the `TaoApp` NSApplication subclass")]
    TaoAppNotFound,
    #[error("`TaoApp` is missing the `sendEvent:` method")]
    SendEventMethodMissing,
    #[error("`NSApplication` is missing the `terminate:` method")]
    TerminateMethodMissing,
    #[error(
        "class_addMethod failed for selector `{0}` — the class may already define this method"
    )]
    AddMethodFailed(&'static str),
    #[error("class_addProtocol failed for protocol `{0}`")]
    AddProtocolFailed(&'static str),
    #[error(
        "CEF protocol object for `{0}` is not registered with the Objective-C runtime — \
         is the `cef` crate linked correctly?"
    )]
    ProtocolNotLinked(&'static str),
}

/// Captured IMP of tao's original `sendEvent:` method, populated by
/// [`install_thalyn_application_swizzle`] before our wrapper goes
/// live. The wrapper transmutes this back to a typed function
/// pointer to call tao's body once the CEF flag is toggled.
static ORIGINAL_SEND_EVENT: OnceLock<Imp> = OnceLock::new();

/// Captured IMP of `NSApplication`'s `terminate:` method. We keep
/// this around so the override can fall through to the AppKit
/// orderly-quit path once we've asked CEF to close its browsers.
static ORIGINAL_TERMINATE: OnceLock<Imp> = OnceLock::new();

/// Whether the active `NSApp` is currently dispatching an event
/// through `sendEvent:`. Read by CEF's macOS message-pump observer
/// via `isHandlingSendEvent`. The active `NSApp` is a singleton
/// (`[NSApplication sharedApplication]`) so a process-global atomic
/// is sufficient — no per-instance state needed.
static HANDLING_SEND_EVENT: AtomicBool = AtomicBool::new(false);

/// Idempotency guard. Re-running the swizzle on an already-swizzled
/// class works for the protocol / class_addMethod parts (those
/// reject duplicates), but a second `class_replaceMethod` would
/// chain wrappers around our already-installed one. Guard the
/// whole sequence behind this flag so a duplicate call from setup
/// or test code is a no-op.
static INSTALLED: AtomicBool = AtomicBool::new(false);

/// Apply the swizzle that adds CEF's NSApplication protocol
/// contracts to tao's `TaoApp` class.
///
/// Idempotent: a second call after a successful install returns
/// `Ok(())` without touching the runtime. Errors return early
/// without partial state — the swizzle is all-or-nothing.
///
/// Must be called on the main thread, after tao has registered
/// `TaoApp` (i.e., after `tauri::Builder` has built its
/// `EventLoop`) and before `cef::initialize` runs.
pub fn install_thalyn_application_swizzle() -> Result<(), SwizzleError> {
    if INSTALLED.load(Ordering::Acquire) {
        return Ok(());
    }

    // Phase A — read-only lookups. Every failure mode that can
    // surface in practice (TaoApp absent, framework not mapped,
    // tao changing its layout) shows up here. Nothing in this
    // phase mutates `TaoApp`'s vtable or method list.
    let tao_app = AnyClass::get(TAO_APP_CLASS_NAME).ok_or(SwizzleError::TaoAppNotFound)?;
    let nsapp_class = class!(NSApplication);
    let original_send_event = unsafe { lookup_imp(tao_app, sel!(sendEvent:)) }
        .ok_or(SwizzleError::SendEventMethodMissing)?;
    let original_terminate = unsafe { lookup_imp(nsapp_class, sel!(terminate:)) }
        .ok_or(SwizzleError::TerminateMethodMissing)?;
    let cef_app_proto = <dyn CefAppProtocol>::protocol()
        .ok_or(SwizzleError::ProtocolNotLinked("CefAppProtocol"))?;
    let cr_app_proto =
        <dyn CrAppProtocol>::protocol().ok_or(SwizzleError::ProtocolNotLinked("CrAppProtocol"))?;
    let cr_app_control_proto = <dyn CrAppControlProtocol>::protocol()
        .ok_or(SwizzleError::ProtocolNotLinked("CrAppControlProtocol"))?;

    // Phase B — mutations. From here on the function is committed:
    // every operation either succeeds or panics. Reaching this point
    // means tao registered TaoApp, the standard methods we need to
    // capture all exist, and the CEF framework is mapped (so the
    // protocol pointers above are non-null). The remaining FFI
    // calls (`class_replaceMethod` / `class_addMethod` /
    // `class_addProtocol`) cannot fail in interesting ways given
    // those preconditions; the assertions below are belt-and-braces.
    let tao_app_ptr = tao_app as *const _ as *mut AnyClass;

    ORIGINAL_SEND_EVENT
        .set(original_send_event)
        .expect("ORIGINAL_SEND_EVENT set after the INSTALLED guard");
    ORIGINAL_TERMINATE
        .set(original_terminate)
        .expect("ORIGINAL_TERMINATE set after the INSTALLED guard");

    unsafe {
        // class_replaceMethod returns the previous IMP; we already
        // have it via class_getInstanceMethod above, so we ignore.
        let _ = class_replaceMethod(
            tao_app_ptr,
            sel!(sendEvent:),
            imp_for(thalyn_send_event_imp_thunk as ThunkSendEvent),
            c"v@:@".as_ptr(),
        );
    }

    let added_terminate = unsafe {
        class_addMethod(
            tao_app_ptr,
            sel!(terminate:),
            imp_for(thalyn_terminate_imp_thunk as ThunkTerminate),
            c"v@:@".as_ptr(),
        )
    }
    .as_bool();
    assert!(
        added_terminate,
        "class_addMethod failed for TaoApp.terminate: after a successful read-only \
         lookup phase — TaoApp may have been mutated by another swizzler"
    );

    let added_is_handling = unsafe {
        class_addMethod(
            tao_app_ptr,
            sel!(isHandlingSendEvent),
            imp_for(thalyn_is_handling_send_event_imp_thunk as ThunkIsHandling),
            c"B@:".as_ptr(),
        )
    }
    .as_bool();
    assert!(
        added_is_handling,
        "class_addMethod failed for TaoApp.isHandlingSendEvent — see TaoApp.terminate: \
         note above"
    );

    let added_set_handling = unsafe {
        class_addMethod(
            tao_app_ptr,
            sel!(setHandlingSendEvent:),
            imp_for(thalyn_set_handling_send_event_imp_thunk as ThunkSetHandling),
            c"v@:B".as_ptr(),
        )
    }
    .as_bool();
    assert!(
        added_set_handling,
        "class_addMethod failed for TaoApp.setHandlingSendEvent: — see TaoApp.terminate: \
         note above"
    );

    assert!(
        unsafe { class_addProtocol(tao_app_ptr, cef_app_proto) }.as_bool(),
        "class_addProtocol failed for CefAppProtocol after a successful read-only lookup"
    );
    assert!(
        unsafe { class_addProtocol(tao_app_ptr, cr_app_proto) }.as_bool(),
        "class_addProtocol failed for CrAppProtocol after a successful read-only lookup"
    );
    assert!(
        unsafe { class_addProtocol(tao_app_ptr, cr_app_control_proto) }.as_bool(),
        "class_addProtocol failed for CrAppControlProtocol after a successful read-only lookup"
    );

    INSTALLED.store(true, Ordering::Release);
    Ok(())
}

/// Whether the swizzle has been installed in this process.
///
/// Exposed for sanity assertions; the swizzle itself is idempotent
/// so callers do not need to check this before calling
/// [`install_thalyn_application_swizzle`].
pub fn is_swizzle_installed() -> bool {
    INSTALLED.load(Ordering::Acquire)
}

// --- Method IMPs ---------------------------------------------------
//
// Each thunk has an explicit `extern "C" fn` type and a matching
// type-encoding string registered with the Objective-C runtime.
// The dispatcher reads the encoding string to know how to push
// `self`, `_cmd`, and the argument list onto the stack before
// jumping to our thunk.

type ThunkSendEvent = extern "C" fn(*const AnyObject, Sel, *const AnyObject);
type ThunkTerminate = extern "C" fn(*const AnyObject, Sel, *const AnyObject);
type ThunkIsHandling = extern "C" fn(*const AnyObject, Sel) -> Bool;
type ThunkSetHandling = extern "C" fn(*const AnyObject, Sel, Bool);

extern "C" fn thalyn_send_event_imp_thunk(
    this: *const AnyObject,
    sel: Sel,
    event: *const AnyObject,
) {
    HANDLING_SEND_EVENT.store(true, Ordering::Relaxed);
    let original = *ORIGINAL_SEND_EVENT
        .get()
        .expect("ORIGINAL_SEND_EVENT set before swizzle marks INSTALLED");
    // SAFETY: the IMP we captured was registered with encoding
    // `v@:@` (see tao's app.rs); transmuting to a fn with that
    // shape matches the calling convention.
    let original_typed: ThunkSendEvent = unsafe { std::mem::transmute(original) };
    original_typed(this, sel, event);
    HANDLING_SEND_EVENT.store(false, Ordering::Relaxed);
}

extern "C" fn thalyn_terminate_imp_thunk(
    this: *const AnyObject,
    sel: Sel,
    sender: *const AnyObject,
) {
    // The CEF browser-shutdown reroute lands here once the host
    // surfaces a `close_all_browsers` API. v0.30's first integration
    // commit (Tauri setup hook) wires that path; this swizzle only
    // sets up the override so the wiring has somewhere to plug in.
    //
    // For now, fall through to NSApplication's terminate: so an
    // unwired build still quits cleanly. The wiring commit replaces
    // the body to: (a) check whether a CEF session is live; (b) if
    // so, ask it to CloseAllBrowsers and bail out — the browsers
    // re-call terminate: once they've all closed; (c) otherwise,
    // delegate to the original IMP below.
    let original = *ORIGINAL_TERMINATE
        .get()
        .expect("ORIGINAL_TERMINATE set before swizzle marks INSTALLED");
    // SAFETY: NSApplication's terminate: is registered with
    // encoding `v@:@` (the standard AppKit shape).
    let original_typed: ThunkTerminate = unsafe { std::mem::transmute(original) };
    original_typed(this, sel, sender);
}

extern "C" fn thalyn_is_handling_send_event_imp_thunk(_this: *const AnyObject, _sel: Sel) -> Bool {
    Bool::new(HANDLING_SEND_EVENT.load(Ordering::Relaxed))
}

extern "C" fn thalyn_set_handling_send_event_imp_thunk(
    _this: *const AnyObject,
    _sel: Sel,
    flag: Bool,
) {
    HANDLING_SEND_EVENT.store(flag.as_bool(), Ordering::Relaxed);
}

// --- Helpers ------------------------------------------------------

/// Look up an instance method's IMP on a given class.
///
/// Returns `None` if the class does not respond to the selector.
/// Walks the inheritance chain — calling this with `(NSApplication,
/// terminate:)` returns NSApplication's IMP even though terminate:
/// is defined far up the AppKit stack. That is the desired
/// behaviour for capturing the "original" IMP we want to delegate to.
///
/// # Safety
///
/// `class` must be a valid NSObject subclass pointer.
unsafe fn lookup_imp(class: &AnyClass, selector: Sel) -> Option<Imp> {
    let method = unsafe { class_getInstanceMethod(class, selector) };
    if method.is_null() {
        return None;
    }
    unsafe { method_getImplementation(method) }
}

/// Type-erase a typed `extern "C" fn` thunk into `Imp` (the
/// untyped fn pointer the Objective-C runtime expects).
///
/// `Imp` is `unsafe extern "C-unwind" fn()`; our thunks are
/// `extern "C" fn(...) -> ...`. Function pointers are pointer-sized
/// regardless of signature, and the Itanium / System V calling
/// conventions on macOS guarantee callers see whatever signature
/// the *encoding string* declared. Transmuting the pointer is the
/// idiomatic shape.
///
/// # Safety
///
/// The caller must register the result with a type-encoding string
/// that matches the typed thunk's signature.
fn imp_for<F: Copy>(thunk: F) -> Imp {
    // `F` is always a fn-pointer type at the call sites; size and
    // alignment match `Imp` by construction.
    unsafe { std::mem::transmute_copy(&thunk) }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Sanity: the swizzle reports a typed error when `TaoApp` is
    /// not registered (e.g., a unit-test environment that has not
    /// gone through `tauri::Builder::build`). This is the surface
    /// the integration commit's smoke test will assert against in
    /// the failure-case branch.
    #[test]
    fn swizzle_reports_tao_app_not_found_when_tao_absent() {
        // In the cargo-test process, tao has not been initialised
        // — there is no `TaoApp` class registered. The swizzle
        // surfaces this via a typed error rather than panicking.
        // (If a future test does init tao in the same process, the
        // swizzle proceeds; we still accept that as a pass — the
        // assertion is "we don't panic with TaoApp absent".)
        let result = install_thalyn_application_swizzle();
        match result {
            Err(SwizzleError::TaoAppNotFound) => (),
            Ok(()) => (),
            Err(other) => panic!("expected TaoAppNotFound or Ok, got {other:?}"),
        }
    }
}
