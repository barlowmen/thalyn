//! macOS NSApplication subclass for `thalyn-cef-host`.
//!
//! CEF requires an NSApplication subclass that implements
//! `CefAppProtocol` / `CrAppProtocol` / `CrAppControlProtocol` so the
//! macOS message-pump observer can ask whether the app is currently
//! inside `sendEvent:`. Without it, CEF crashes with
//! `Check failed: nesting_level_ != 0` on the first event.
//!
//! The subclass also overrides `terminate:` so the orderly-quit
//! machinery routes through `CloseAllBrowsers` instead of the default
//! `[NSApplication terminate:]`, which calls `exit()` and skips the
//! CEF shutdown. The pattern is the cefsimple one; the long-form
//! rationale lives in `docs/spikes/2026-04-30-cef-macos-message-loop.md`.

use std::cell::Cell;
use std::ptr;

use cef::application_mac::{CefAppProtocol, CrAppControlProtocol, CrAppProtocol};
use objc2::{
    define_class, extern_methods, msg_send,
    rc::Retained,
    runtime::{AnyObject, Bool, NSObject, NSObjectProtocol, ProtocolObject},
    sel, ClassType, DefinedClass, MainThreadMarker, MainThreadOnly,
};
use objc2_app_kit::{
    NSApp, NSApplication, NSApplicationDelegate, NSApplicationTerminateReply, NSEvent,
    NSUserInterfaceValidations, NSValidatedUserInterfaceItem,
};
use objc2_foundation::{ns_string, NSBundle, NSObjectNSThreadPerformAdditions};

use super::client::ThalynChildHandler;

define_class! {
    #[unsafe(super(NSObject))]
    #[thread_kind = MainThreadOnly]
    pub struct ThalynChildAppDelegate;

    impl ThalynChildAppDelegate {
        #[unsafe(method(createApplication:))]
        unsafe fn create_application(&self, _object: Option<&AnyObject>) {
            let app = NSApp(MainThreadMarker::new().expect("not on the main thread"));
            assert!(app.isKindOfClass(ThalynChildApplication::class()));
            assert!(app
                .delegate()
                .unwrap()
                .isKindOfClass(ThalynChildAppDelegate::class()));

            // Load MainMenu.nib if present in the bundle. The bundled
            // app ships its own; for unbundled dev runs the call is a
            // best-effort no-op.
            let main_bundle = NSBundle::mainBundle();
            let _: Bool = msg_send![&main_bundle,
                loadNibNamed: ns_string!("MainMenu"),
                owner: &*app,
                topLevelObjects: ptr::null_mut::<*const AnyObject>()
            ];
        }
    }

    unsafe impl NSObjectProtocol for ThalynChildAppDelegate {}

    unsafe impl NSApplicationDelegate for ThalynChildAppDelegate {
        #[unsafe(method(applicationShouldTerminate:))]
        unsafe fn application_should_terminate(
            &self,
            _sender: &NSApplication,
        ) -> NSApplicationTerminateReply {
            NSApplicationTerminateReply::TerminateNow
        }

        #[unsafe(method(applicationShouldHandleReopen:hasVisibleWindows:))]
        unsafe fn application_should_handle_reopen(
            &self,
            _sender: &NSApplication,
            _has_visible: Bool,
        ) -> Bool {
            // The parent process drives reopen via OS child-window
            // mechanics; do nothing here.
            Bool::NO
        }

        #[unsafe(method(applicationSupportsSecureRestorableState:))]
        unsafe fn application_supports_secure_restorable_state(
            &self,
            _sender: &NSApplication,
        ) -> Bool {
            Bool::YES
        }
    }

    unsafe impl NSUserInterfaceValidations for ThalynChildAppDelegate {
        #[unsafe(method(validateUserInterfaceItem:))]
        unsafe fn validate_user_interface_item(
            &self,
            _item: &ProtocolObject<dyn NSValidatedUserInterfaceItem>,
        ) -> Bool {
            Bool::NO
        }
    }
}

impl ThalynChildAppDelegate {
    fn new(mtm: MainThreadMarker) -> Retained<Self> {
        let this = ThalynChildAppDelegate::alloc(mtm).set_ivars(());
        unsafe { msg_send![super(this), init] }
    }
}

/// Instance variables of `ThalynChildApplication`.
#[derive(Default)]
pub struct ThalynChildApplicationIvars {
    handling_send_event: Cell<Bool>,
}

define_class!(
    /// `NSApplication` subclass that implements the CEF protocols.
    /// Mirrors cefsimple's `SimpleApplication` shape.
    #[unsafe(super(NSApplication))]
    #[ivars = ThalynChildApplicationIvars]
    pub struct ThalynChildApplication;

    impl ThalynChildApplication {
        #[unsafe(method(sendEvent:))]
        unsafe fn send_event(&self, event: &NSEvent) {
            let was_sending = self.is_handling_send_event();
            if !was_sending {
                self.set_handling_send_event(true);
            }
            let _: () = msg_send![super(self), sendEvent: event];
            if !was_sending {
                self.set_handling_send_event(false);
            }
        }

        /// Re-route Cocoa's `terminate:` through CEF's
        /// `CloseAllBrowsers`. The default implementation calls
        /// `exit()`, which cuts off CEF's shutdown.
        #[unsafe(method(terminate:))]
        unsafe fn terminate(&self, _sender: &AnyObject) {
            if let Some(handler) = ThalynChildHandler::instance() {
                let mut handler = handler.lock().expect("ThalynChildHandler mutex poisoned");
                if !handler.is_closing() {
                    handler.close_all_browsers(false);
                }
            }
        }
    }

    unsafe impl CrAppControlProtocol for ThalynChildApplication {
        #[unsafe(method(setHandlingSendEvent:))]
        unsafe fn _set_handling_send_event(&self, handling_send_event: Bool) {
            self.ivars().handling_send_event.set(handling_send_event);
        }
    }

    unsafe impl CrAppProtocol for ThalynChildApplication {
        #[unsafe(method(isHandlingSendEvent))]
        unsafe fn _is_handling_send_event(&self) -> Bool {
            self.ivars().handling_send_event.get()
        }
    }

    unsafe impl CefAppProtocol for ThalynChildApplication {}
);

impl ThalynChildApplication {
    extern_methods! {
        #[unsafe(method(sharedApplication))]
        fn shared_application() -> Retained<Self>;

        #[unsafe(method(setHandlingSendEvent:))]
        fn set_handling_send_event(&self, handling_send_event: bool);

        #[unsafe(method(isHandlingSendEvent))]
        fn is_handling_send_event(&self) -> bool;
    }
}

pub fn setup_application() {
    // First touch wins: NSApp is locked to whichever subclass calls
    // `sharedApplication` first. Touching it before any other AppKit
    // code keeps CEF happy.
    let _ = ThalynChildApplication::shared_application();

    let mtm = MainThreadMarker::new().expect("not on the main thread");
    assert!(NSApp(mtm).isKindOfClass(ThalynChildApplication::class()));
}

pub fn setup_app_delegate() -> Retained<ThalynChildAppDelegate> {
    let mtm = MainThreadMarker::new().expect("not on the main thread");
    let delegate = ThalynChildAppDelegate::new(mtm);
    let proto = ProtocolObject::<dyn NSApplicationDelegate>::from_retained(delegate.clone());
    let app = NSApp(mtm);
    assert!(app.isKindOfClass(ThalynChildApplication::class()));
    app.setDelegate(Some(&proto));
    assert!(app
        .delegate()
        .unwrap()
        .isKindOfClass(ThalynChildAppDelegate::class()));

    unsafe {
        delegate.performSelectorOnMainThread_withObject_waitUntilDone(
            sel!(createApplication:),
            None,
            false,
        );
    }
    delegate
}
