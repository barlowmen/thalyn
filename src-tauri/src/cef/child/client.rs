//! `cef::Client` + life-span handlers for `thalyn-cef-host`.
//!
//! Tracks the in-flight `Browser` instances so that:
//!
//! - `terminate:` (macOS dock-quit / cmd-Q) can close every browser
//!   cleanly through CEF's own `CloseBrowser` machinery rather than
//!   `exit()`-ing under CEF, and
//! - the message loop quits as soon as the last browser is gone.
//!
//! The brain attaches over CDP and drives navigation; this handler
//! does *not* customise titles, error pages, or display behaviour —
//! the cefsimple example's `DisplayHandler` / `LoadHandler`
//! customisations are left out intentionally, since the brain
//! supplies its own loaded-state tracking via CDP.

use std::sync::{Arc, Mutex, OnceLock, Weak};

use cef::*;

static HANDLER_INSTANCE: OnceLock<Weak<Mutex<ThalynChildHandler>>> = OnceLock::new();

pub struct ThalynChildHandler {
    browsers: Vec<Browser>,
    is_closing: bool,
    weak_self: Weak<Mutex<Self>>,
}

impl ThalynChildHandler {
    pub fn instance() -> Option<Arc<Mutex<Self>>> {
        HANDLER_INSTANCE.get().and_then(|weak| weak.upgrade())
    }

    pub fn new() -> Arc<Mutex<Self>> {
        Arc::new_cyclic(|weak| {
            if let Err(existing) = HANDLER_INSTANCE.set(weak.clone()) {
                assert_eq!(
                    existing.strong_count(),
                    0,
                    "ThalynChildHandler singleton already populated by a live instance"
                );
            }
            Mutex::new(Self {
                browsers: Vec::new(),
                is_closing: false,
                weak_self: weak.clone(),
            })
        })
    }

    pub fn is_closing(&self) -> bool {
        self.is_closing
    }

    fn on_after_created(&mut self, browser: Option<&mut Browser>) {
        debug_assert_ne!(currently_on(ThreadId::UI), 0);
        if let Some(browser) = browser.cloned() {
            self.browsers.push(browser);
        }
    }

    fn do_close(&mut self, _browser: Option<&mut Browser>) -> bool {
        debug_assert_ne!(currently_on(ThreadId::UI), 0);
        if self.browsers.len() == 1 {
            self.is_closing = true;
        }
        // Allow the OS close event to proceed.
        false
    }

    fn on_before_close(&mut self, browser: Option<&mut Browser>) {
        debug_assert_ne!(currently_on(ThreadId::UI), 0);
        let mut browser = match browser.cloned() {
            Some(b) => b,
            None => return,
        };
        if let Some(idx) = self
            .browsers
            .iter()
            .position(move |entry| entry.is_same(Some(&mut browser)) != 0)
        {
            self.browsers.remove(idx);
        }
        if self.browsers.is_empty() {
            quit_message_loop();
        }
    }

    pub fn close_all_browsers(&mut self, force_close: bool) {
        let thread_id = ThreadId::UI;
        if currently_on(thread_id) == 0 {
            let me = self
                .weak_self
                .upgrade()
                .expect("ThalynChildHandler singleton dropped while close_all_browsers was queued");
            let mut task = CloseAllBrowsers::new(me, force_close);
            post_task(thread_id, Some(&mut task));
            return;
        }
        for browser in self.browsers.iter() {
            let host = browser.host().expect("BrowserHost is None");
            host.close_browser(force_close.into());
        }
    }
}

wrap_client! {
    pub struct ThalynChildClient {
        inner: Arc<Mutex<ThalynChildHandler>>,
    }

    impl Client {
        fn life_span_handler(&self) -> Option<LifeSpanHandler> {
            Some(ThalynChildLifeSpanHandler::new(self.inner.clone()))
        }
    }
}

wrap_life_span_handler! {
    struct ThalynChildLifeSpanHandler {
        inner: Arc<Mutex<ThalynChildHandler>>,
    }

    impl LifeSpanHandler {
        fn on_after_created(&self, browser: Option<&mut Browser>) {
            let mut handler = self
                .inner
                .lock()
                .expect("ThalynChildHandler mutex poisoned");
            handler.on_after_created(browser);
        }

        fn do_close(&self, browser: Option<&mut Browser>) -> i32 {
            let mut handler = self
                .inner
                .lock()
                .expect("ThalynChildHandler mutex poisoned");
            handler.do_close(browser).into()
        }

        fn on_before_close(&self, browser: Option<&mut Browser>) {
            let mut handler = self
                .inner
                .lock()
                .expect("ThalynChildHandler mutex poisoned");
            handler.on_before_close(browser);
        }
    }
}

wrap_task! {
    struct CloseAllBrowsers {
        inner: Arc<Mutex<ThalynChildHandler>>,
        force_close: bool,
    }

    impl Task {
        fn execute(&self) {
            debug_assert_ne!(currently_on(ThreadId::UI), 0);
            let mut handler = self
                .inner
                .lock()
                .expect("ThalynChildHandler mutex poisoned");
            handler.close_all_browsers(self.force_close);
        }
    }
}
