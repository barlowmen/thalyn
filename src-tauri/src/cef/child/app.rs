//! `cef::App` and `cef::BrowserProcessHandler` for `thalyn-cef-host`.
//!
//! The handler creates a single native Chromium top-level window
//! against the per-Thalyn profile. Views (the cross-platform
//! Chromium widget toolkit) is intentionally *not* used; the
//! v0.29 phase exit criterion is "full Chromium capability
//! including passkeys / DRM / IME / drag-drop", which want a real
//! native window with the platform input client. The user-visible
//! window will be re-parented to the Tauri main window at runtime
//! via OS child-window APIs (`NSWindow.addChildWindow:` on macOS,
//! `SetParent` on Windows, X11 `XReparentWindow`).

use std::cell::RefCell;

use cef::*;

use super::client::{ThalynChildClient, ThalynChildHandler};

wrap_app! {
    pub struct ThalynChildApp {
        initial_url: String,
    }

    impl App {
        fn browser_process_handler(&self) -> Option<BrowserProcessHandler> {
            Some(ThalynChildBrowserProcessHandler::new(
                self.initial_url.clone(),
                RefCell::new(None),
            ))
        }
    }
}

wrap_browser_process_handler! {
    struct ThalynChildBrowserProcessHandler {
        initial_url: String,
        client: RefCell<Option<Client>>,
    }

    impl BrowserProcessHandler {
        fn on_context_initialized(&self) {
            debug_assert_ne!(currently_on(ThreadId::UI), 0);

            // Default to Chrome runtime style. Alloy stays available
            // behind --use-alloy-style for parity with cefsimple, but
            // Chrome is the v0.29 ship since the user-facing surface
            // wants the chromium chrome (passkey UI, omnibox prompts,
            // print preview, etc.) the alloy variant strips down.
            let command_line = command_line_get_global()
                .expect("global cef command line must be available after initialize");
            let use_alloy_style =
                command_line.has_switch(Some(&CefString::from("use-alloy-style"))) != 0;
            let runtime_style = if use_alloy_style {
                RuntimeStyle::ALLOY
            } else {
                RuntimeStyle::DEFAULT
            };

            // Stash the client so re-creation paths (popup browsers,
            // dock-icon reopen) reuse the same handler graph.
            {
                let handler = ThalynChildHandler::new();
                let mut client = self.client.borrow_mut();
                *client = Some(ThalynChildClient::new(handler));
            }

            let url_arg = CefString::from(
                &command_line.switch_value(Some(&CefString::from("url")))
            ).to_string();
            let url = if url_arg.is_empty() {
                self.initial_url.as_str()
            } else {
                url_arg.as_str()
            };
            let url_cef = CefString::from(url);

            let window_info = WindowInfo {
                runtime_style,
                ..Default::default()
            };
            #[cfg(target_os = "windows")]
            let window_info = window_info.set_as_popup(Default::default(), "thalyn-cef-host");

            let browser_settings = BrowserSettings::default();
            let mut client = self.default_client();
            browser_host_create_browser(
                Some(&window_info),
                client.as_mut(),
                Some(&url_cef),
                Some(&browser_settings),
                None,
                None,
            );
        }

        fn default_client(&self) -> Option<Client> {
            self.client.borrow().clone()
        }
    }
}
