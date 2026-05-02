//! Process-global host `NSView` that CEF parents its Browser to.
//!
//! v0.30 lands the literal in-process embedding ADR-0019 specified:
//! the user-facing `Browser` paints into a child `NSView` of the
//! Tauri main window's content view. The wiring is:
//!
//! 1. [`install`] runs once from inside Tauri's setup hook, after
//!    Tauri has built its main window (so `WebviewWindow::ns_view`
//!    returns the contentView pointer). It creates a sibling
//!    [`NSView`] under that contentView, stashes a retained handle
//!    in [`HOST`], and returns the new view's pointer to the caller
//!    so [`super::app::ThalynApp`] can pass it to
//!    `browser_host_create_browser` as `cef_window_info_t::parent_view`.
//! 2. The renderer's `cef_set_window_rect` Tauri command lands the
//!    drawer-host rect in [`crate::cef::CefHost::set_window_rect`],
//!    which calls [`set_frame`]. The work is dispatched to the
//!    main thread (NSView mutations have to land there) and the
//!    host view's frame is updated from the requested rect.
//! 3. CEF's content NSView (created by `browser_host_create_browser`
//!    inside our host view) carries default autoresizing masks so
//!    it tracks the parent's bounds.
//!
//! Coordinate translation: HTML rects come in CSS pixels with `y=0`
//! at the top; macOS NSView frames are in points (== CSS pixels at
//! the typical retina dpr=2 the project targets) with `y=0` at the
//! bottom of the parent view. The y-flip is applied in [`set_frame`]
//! using the parent's current height read on the main thread.
//!
//! The module is a no-op outside macOS; Windows and Linux paths land
//! alongside in follow-on commits.

#![cfg(all(feature = "cef", target_os = "macos"))]
#![allow(dead_code)]

use std::ffi::c_void;
use std::sync::{Mutex, OnceLock};

use objc2::rc::Retained;
use objc2::{msg_send, MainThreadOnly};
use objc2_app_kit::NSView;
use objc2_foundation::{MainThreadMarker, NSPoint, NSRect, NSSize};

use crate::cef::HostWindowRect;

/// Process-global host view + the latest rect the renderer pushed
/// while we wait for a main-thread tick.
struct HostView {
    /// Our child NSView, parented under Tauri's contentView. Holds
    /// the retained pointer for the process lifetime — the Browser
    /// paints into a subview of this.
    view: Retained<NSView>,
    /// Tauri's contentView, retained alongside ours so frame reads
    /// (for the y-flip) and resize-on-parent semantics stay valid.
    parent: Retained<NSView>,
    /// The most recent rect the renderer reported. Latest-wins —
    /// when multiple set_frame calls coalesce on the same main-thread
    /// tick we apply the most recent only.
    pending: Mutex<Option<HostWindowRect>>,
}

// SAFETY: NSView pointers are not Send/Sync but we gate every
// mutation behind a main-thread dispatch. The handle is read-only
// from non-main threads (and only used to enqueue work that runs on
// main).
unsafe impl Send for HostView {}
unsafe impl Sync for HostView {}

static HOST: OnceLock<HostView> = OnceLock::new();

#[derive(Debug, thiserror::Error)]
pub enum InstallError {
    #[error("host view already installed; install() must run exactly once")]
    AlreadyInstalled,
    #[error("Tauri main window did not surface a usable NSView pointer")]
    ParentViewMissing,
}

/// Install the host view as a child of Tauri's contentView.
///
/// `parent_ns_view` is the pointer returned by Tauri's
/// `WebviewWindow::ns_view()`. Must run on the main thread —
/// callers are responsible for that (the Tauri setup hook runs on
/// main by construction).
///
/// Returns the new host-view pointer, suitable to pass as
/// `cef_window_info_t::parent_view` to `browser_host_create_browser`.
/// On `Err`, no state is written and the engine path falls back to
/// the warning-logged "no chrome" mode in [`super::app`].
pub fn install(
    parent_ns_view: *mut c_void,
    mtm: MainThreadMarker,
) -> Result<*mut c_void, InstallError> {
    if HOST.get().is_some() {
        return Err(InstallError::AlreadyInstalled);
    }
    if parent_ns_view.is_null() {
        return Err(InstallError::ParentViewMissing);
    }

    // SAFETY: Tauri hands us an NSView pointer via raw_window_handle;
    // it is non-null per the check above and lives for at least as
    // long as the Tauri main window. We retain it so its lifetime is
    // independent of any Tauri-side handle juggling.
    let parent: Retained<NSView> = unsafe { Retained::retain(parent_ns_view as *mut NSView) }
        .ok_or_else(|| {
            tracing::error!(
                target = "thalyn::cef",
                "Tauri ns_view pointer could not be retained"
            );
            InstallError::ParentViewMissing
        })?;

    // Build the host view at zero rect; the first renderer rect push
    // will resize it.
    let zero = NSRect {
        origin: NSPoint { x: 0.0, y: 0.0 },
        size: NSSize {
            width: 0.0,
            height: 0.0,
        },
    };
    // SAFETY: `NSView::alloc + initWithFrame:` is the standard AppKit
    // construction path; both calls are valid on the main thread.
    let view: Retained<NSView> = unsafe {
        let alloc = NSView::alloc(mtm);
        msg_send![alloc, initWithFrame: zero]
    };

    // Add as a subview of Tauri's contentView. We deliberately add
    // *above* (i.e. after) the existing webview so the parented
    // Browser draws on top of the Tauri renderer when present.
    parent.addSubview(&view);

    let raw_view_ptr: *mut NSView = Retained::as_ptr(&view) as *mut NSView;

    HOST.set(HostView {
        view,
        parent,
        pending: Mutex::new(None),
    })
    .map_err(|_| InstallError::AlreadyInstalled)?;

    tracing::debug!(
        target = "thalyn::cef",
        view = ?raw_view_ptr,
        "host NSView installed under Tauri contentView"
    );

    Ok(raw_view_ptr.cast())
}

/// Pointer to the installed host NSView, or null if [`install`] has
/// not run successfully. Read by [`super::app::build_window_info`].
pub fn current_handle() -> *mut c_void {
    HOST.get()
        .map(|h| Retained::as_ptr(&h.view) as *mut c_void)
        .unwrap_or(std::ptr::null_mut())
}

/// Update the host view's frame from a renderer-reported rect.
///
/// Safe to call from any thread; the actual `setFrame:` always runs
/// on the main thread. If [`install`] has not run, the call is a
/// no-op (the Browser-creation path will have logged its absence).
pub fn set_frame(rect: HostWindowRect) {
    let Some(host) = HOST.get() else {
        return;
    };
    {
        let mut guard = host.pending.lock().expect("HostView::pending poisoned");
        *guard = Some(rect);
    }
    dispatch_to_main(apply_pending_frame_main_thread);
}

extern "C" fn apply_pending_frame_main_thread(_ctx: *mut c_void) {
    // We're now on the main thread — `MainThreadMarker::new` should
    // succeed. If it doesn't (e.g. someone wired a non-main dispatch
    // queue), fail safely.
    let Some(_mtm) = MainThreadMarker::new() else {
        tracing::error!(
            target = "thalyn::cef",
            "host-view frame update fired off the main thread"
        );
        return;
    };
    let Some(host) = HOST.get() else {
        return;
    };
    let Some(rect) = host
        .pending
        .lock()
        .expect("HostView::pending poisoned")
        .take()
    else {
        return;
    };

    // Read the parent's height to flip y from HTML's top-origin to
    // AppKit's bottom-origin coordinate space.
    let parent_height = host.parent.bounds().size.height;
    let flipped_y = (parent_height - rect.y - rect.height).max(0.0);
    let frame = NSRect {
        origin: NSPoint {
            x: rect.x,
            y: flipped_y,
        },
        size: NSSize {
            width: rect.width.max(0.0),
            height: rect.height.max(0.0),
        },
    };
    host.view.setFrame(frame);
}

// libdispatch FFI for posting work to the main queue. Standard
// Apple-shipped library, no extra crate. Note that
// `dispatch_get_main_queue()` is a *macro* in `dispatch/queue.h`
// that expands to `&_dispatch_main_q` — the real symbol is the
// queue object itself, not a function. Bind to that directly.
#[link(name = "System", kind = "dylib")]
unsafe extern "C" {
    static _dispatch_main_q: c_void;
    fn dispatch_async_f(queue: *mut c_void, context: *mut c_void, work: extern "C" fn(*mut c_void));
}

fn dispatch_to_main(work: extern "C" fn(*mut c_void)) {
    unsafe {
        let queue = std::ptr::addr_of!(_dispatch_main_q).cast_mut();
        dispatch_async_f(queue, std::ptr::null_mut(), work);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// `current_handle` returns null before install runs. Cheap
    /// no-cost regression assertion.
    #[test]
    fn current_handle_is_null_before_install() {
        // The OnceLock is process-global; we can't reset it across
        // tests safely. This test runs first by alphabetical order
        // (`a` < `c` in cargo test naming) so HOST is empty.
        assert!(current_handle().is_null());
    }

    /// `set_frame` is a no-op when no host view is installed —
    /// asserts that the early-return branch holds without panicking.
    #[test]
    fn set_frame_without_install_is_a_noop() {
        let rect = HostWindowRect {
            x: 10.0,
            y: 20.0,
            width: 100.0,
            height: 200.0,
        };
        // Should not panic, should not enqueue anything observable.
        set_frame(rect);
    }
}
