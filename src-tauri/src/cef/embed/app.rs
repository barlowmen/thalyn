//! CEF App + Client + BrowserProcessHandler wrappers.
//!
//! `cef::initialize` accepts an optional `App` whose
//! `BrowserProcessHandler::on_context_initialized` is called on the
//! browser-process UI thread once the CEF context is ready. That
//! callback is the only place an embedded host can call
//! `browser_host_create_browser` to spin up the user-facing
//! `Browser` parented to a Tauri-owned native view.
//!
//! The pieces here are intentionally minimal: `ThalynClient` ships
//! all-default handlers (CDP attaches via the in-process WS URL, so
//! we don't need a custom `LifeSpanHandler` to track lifecycle from
//! Rust today) and `ThalynBrowserProcessHandler` reads the live
//! host-view pointer from [`crate::cef::embed::host_view`] when CEF
//! signals `on_context_initialized`. v0.30 keeps the URL pinned to
//! `about:blank`; the renderer drives navigation through CDP via
//! the `browser_*` brain tools that v1 already shipped.
//!
//! The wrappers are all process-global by construction — there is
//! one CEF runtime per process, so one `ThalynApp`, one
//! `ThalynBrowserProcessHandler`, and one `Client` is sufficient.

#![cfg(feature = "cef")]
#![allow(dead_code)]

use std::cell::RefCell;

use cef::rc::Rc;
use cef::{
    browser_host_create_browser, currently_on, wrap_app, wrap_browser_process_handler, wrap_client,
    App, BrowserProcessHandler, BrowserSettings, CefString, Client, ImplApp,
    ImplBrowserProcessHandler, ImplClient, Rect, ThreadId, WindowInfo, WrapApp,
    WrapBrowserProcessHandler, WrapClient,
};

#[cfg(target_os = "macos")]
use super::host_view;

/// Initial URL the parented Browser navigates to. The user-driven
/// browser drawer or the brain's CDP tools take over from here.
const INITIAL_URL: &str = "about:blank";

wrap_client! {
    pub struct ThalynClient;

    impl Client {
        // No overrides — every handler defaults to None. CDP
        // attaches through the WS URL surfaced by the
        // `DevToolsActivePort` watcher; nothing on the Client surface
        // is load-bearing for v0.30.
    }
}

wrap_browser_process_handler! {
    pub struct ThalynBrowserProcessHandler {
        client: RefCell<Option<Client>>,
    }

    impl BrowserProcessHandler {
        fn on_context_initialized(&self) {
            // CEF guarantees this fires on the UI thread; the assert
            // is cheap insurance against a future refactor that
            // accidentally posts the call through a worker pool.
            debug_assert_ne!(currently_on(ThreadId::UI), 0);

            let mut client_slot = self.client.borrow_mut();
            if client_slot.is_none() {
                *client_slot = Some(ThalynClient::new());
            }
            let mut client = client_slot.clone();

            let url = CefString::from(INITIAL_URL);
            let settings = BrowserSettings::default();
            let window_info = build_window_info();

            let created = browser_host_create_browser(
                Some(&window_info),
                client.as_mut(),
                Some(&url),
                Some(&settings),
                None,
                None,
            );
            if created == 0 {
                tracing::error!(
                    target = "thalyn::cef",
                    "browser_host_create_browser returned 0; the parented Browser \
                     will not appear and CDP will have no Page targets"
                );
            } else {
                tracing::info!(
                    target = "thalyn::cef",
                    "parented CEF Browser created"
                );
            }
        }
    }
}

wrap_app! {
    pub struct ThalynApp {
        handler: RefCell<Option<BrowserProcessHandler>>,
    }

    impl App {
        fn browser_process_handler(&self) -> Option<BrowserProcessHandler> {
            // CEF calls this once during initialisation and caches
            // the result. Build the handler lazily so the App can be
            // constructed before the host view exists.
            let mut slot = self.handler.borrow_mut();
            if slot.is_none() {
                *slot = Some(ThalynBrowserProcessHandler::new(RefCell::new(None)));
            }
            slot.clone()
        }
    }
}

impl ThalynApp {
    /// Construct the App in its initial empty state. CEF builds its
    /// internal handler cache the first time `browser_process_handler`
    /// is queried.
    pub fn build() -> App {
        ThalynApp::new(RefCell::new(None))
    }
}

/// Build the [`WindowInfo`] that points CEF's Browser at the
/// process-global host view. On macOS this reads the host-view
/// pointer installed by [`host_view::install`]; on other platforms
/// the parent handle is filled in by the platform path. v0.30 ships
/// the macOS path; Windows + Linux follow.
///
/// The initial bounds passed here size the `_cef_window_info_t::bounds`
/// rectangle CEF uses for its own content NSView inside our host
/// view. We give it a placeholder; the real size lands on the host
/// view via the renderer's `cef_set_window_rect` plumbing, and
/// CEF's child autoresizes to match.
fn build_window_info() -> WindowInfo {
    let placeholder_bounds = Rect {
        x: 0,
        y: 0,
        width: 800,
        height: 600,
    };
    #[cfg(target_os = "macos")]
    {
        let parent = host_view::current_handle();
        if parent.is_null() {
            tracing::warn!(
                target = "thalyn::cef",
                "host view is not installed; the Browser will be parented to a \
                 detached NSView and the user will not see any chrome"
            );
        }
        WindowInfo::default().set_as_child(parent, &placeholder_bounds)
    }
    #[cfg(not(target_os = "macos"))]
    {
        WindowInfo::default().set_as_child(std::ptr::null_mut(), &placeholder_bounds)
    }
}
