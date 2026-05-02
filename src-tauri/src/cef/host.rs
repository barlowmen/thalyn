//! CEF lifecycle owner — public API surface the rest of the engine
//! consumes.
//!
//! Per ADR-0029, v0.30 runs CEF in-process inside the Tauri main
//! process. The engine itself is initialised once during the Tauri
//! setup hook (see [`crate::cef::embed::runtime::initialize_cef_engine`])
//! and is process-global from that point on; this module is the
//! state machine the renderer + brain consume on top of it.
//!
//! The renderer's `browser_*` Tauri commands and the brain's CDP
//! attachment both speak the [`HostState`] surface. Once
//! [`CefHost::attach_to_active_engine`] reads
//! `DevToolsActivePort` from the active profile, the host
//! transitions to [`HostState::Running { ws_url, .. }`] and the
//! brain attaches via JSON-RPC `browser.attach` to that WS URL.
//!
//! [`CefHost::start`] and [`CefHost::stop`] are retained on the
//! API surface for renderer compatibility; in the in-process
//! world they are effectively no-ops (the engine is always
//! either running or in a terminal failure state from setup-hook
//! init). Future work (native-view parenting, multi-Browser tab
//! UI) will reshape these to `create_browser` / `close_browser`
//! once those concerns are in scope.

use std::path::{Path, PathBuf};
use std::sync::Arc;

use thiserror::Error;
use tokio::sync::{watch, Mutex, RwLock};

use super::port_file::{wait_for_port_file, DevToolsEndpoint, PortFileError};
use super::profile::{CefProfile, ProfileError};
use super::sdk::SdkResolveError;

/// Drawer-host rectangle the renderer reports via
/// `cef_set_window_rect`. Coordinates are in CSS pixels relative to
/// the Tauri main window's content view (which equal macOS points at
/// the typical Retina devicePixelRatio of 2). The native-view
/// parenting layer (lands in a follow-on commit) reads this rect
/// when sizing the CEF Browser's parented `NSView` /
/// `HWND` / `GtkWidget`.
#[derive(Debug, Clone, Copy, PartialEq, serde::Deserialize, serde::Serialize)]
pub struct HostWindowRect {
    pub x: f64,
    pub y: f64,
    pub width: f64,
    pub height: f64,
}

/// Public state of the CEF engine. Mirrors v1's `BrowserState`
/// shape so the renderer can render a consistent panel through the
/// engine swap.
#[derive(Debug, Clone, serde::Serialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum HostState {
    /// Engine has not been initialised. This is the state at app
    /// startup before the Tauri setup hook runs, and the state if
    /// `cef::initialize` failed (e.g. unbundled dev runs without
    /// the helper-bundle layout).
    Idle,
    /// Engine initialised; we are polling for `DevToolsActivePort`
    /// to come up.
    Starting { profile_dir: String },
    /// Engine running; the brain can attach to `ws_url`.
    Running {
        ws_url: String,
        profile_dir: String,
        /// The pinned CEF version baked into the binary at build
        /// time (`src-tauri/cef-version.txt`). Surfaced for the
        /// renderer's diagnostics chrome; not load-bearing.
        sdk_version: String,
    },
    /// The engine ended, or its initialisation failed. `reason` is
    /// human-readable.
    Exited { reason: String },
}

#[derive(Debug, Error)]
pub enum HostError {
    #[error("CEF SDK could not be located: {0}")]
    SdkResolve(#[from] SdkResolveError),
    #[error("CEF profile could not be opened: {0}")]
    Profile(#[from] ProfileError),
    #[error("port file watcher failed: {0}")]
    PortFile(#[from] PortFileError),
    #[error(
        "the in-process CEF engine is not initialised; the Tauri setup hook \
         did not call `cef::initialize` (likely an unbundled dev run with \
         no helper-bundle layout next to the parent exe)"
    )]
    EngineNotInitialized,
    #[error("the CEF engine is already attached to this CefHost")]
    AlreadyAttached,
}

/// Handle to the active CEF engine: the per-Thalyn profile dir,
/// the DevTools endpoint the brain attaches to, and the
/// drawer-host rect the native-view parenting layer reads. There
/// is at most one `CefSession` per process (the in-process CEF
/// runtime is process-global).
pub struct CefSession {
    profile: CefProfile,
    endpoint: DevToolsEndpoint,
    /// Latest drawer-host rect the renderer has reported, shared
    /// with the native-view parenting layer.
    window_rect: Arc<Mutex<Option<HostWindowRect>>>,
}

impl CefSession {
    pub fn ws_url(&self) -> String {
        self.endpoint.ws_url()
    }

    pub fn profile_dir(&self) -> &Path {
        self.profile.dir()
    }

    pub fn endpoint(&self) -> &DevToolsEndpoint {
        &self.endpoint
    }

    /// Read the latest drawer-host rect the renderer has reported,
    /// if any. The native-view parenting layer consumes this when
    /// applying its native parent-child relationship.
    pub async fn current_window_rect(&self) -> Option<HostWindowRect> {
        *self.window_rect.lock().await
    }

    /// Update the drawer-host rect. Called from the
    /// `cef_set_window_rect` Tauri command; idempotent and safe to
    /// call before, during, or after the parenting layer wires up.
    pub async fn set_window_rect(&self, rect: HostWindowRect) {
        *self.window_rect.lock().await = Some(rect);
    }
}

/// Owner of the CEF runtime state. Single instance per app —
/// `cef::initialize` / `cef::shutdown` are process-global.
///
/// Lifecycle in v0.30:
///
/// 1. `CefHost::new(profile_root)` — constructed in
///    `init_app_state` after `cef::initialize` ran in the setup
///    hook. Holds the profile root for the active engine.
/// 2. `CefHost::attach_to_active_engine()` — called once after
///    construction. Polls `DevToolsActivePort`, surfaces the WS
///    URL via [`HostState::Running`].
/// 3. `CefHost::start` / `CefHost::stop` — no-ops in the
///    in-process world; the engine is always either running or
///    terminally failed. Retained on the API surface so the
///    renderer's `browser_start` / `browser_stop` Tauri commands
///    do not need to change shape.
pub struct CefHost {
    profile_root: PathBuf,
    inner: RwLock<Option<CefSession>>,
    state_tx: watch::Sender<HostState>,
    /// Held so consumers can subscribe before any session starts.
    _state_rx: watch::Receiver<HostState>,
    /// Latest rect the renderer has pushed before a session
    /// existed. On `attach_to_active_engine`, the new session
    /// adopts this rect so the parenting layer can apply it
    /// immediately rather than waiting for the next renderer tick.
    pending_rect: Mutex<Option<HostWindowRect>>,
}

impl CefHost {
    pub fn new(profile_root: PathBuf) -> Self {
        let (tx, rx) = watch::channel(HostState::Idle);
        Self {
            profile_root,
            inner: RwLock::new(None),
            state_tx: tx,
            _state_rx: rx,
            pending_rect: Mutex::new(None),
        }
    }

    /// The profile root the engine was configured against. Stable
    /// for the lifetime of this `CefHost`; used by
    /// [`Self::attach_to_active_engine`] to read
    /// `DevToolsActivePort` from the right directory.
    pub fn profile_root(&self) -> &Path {
        &self.profile_root
    }

    /// Update the drawer-host rect. Routed from the
    /// `cef_set_window_rect` Tauri command. If a session is live
    /// the rect lands on it; otherwise it is held as the
    /// next-session pending rect so a fresh session adopts it
    /// without a renderer round-trip.
    ///
    /// On macOS the rect is also forwarded to the
    /// [`crate::cef::embed::host_view`] module so the parented
    /// `NSView` resizes in lockstep with the drawer chrome. The
    /// host-view path is feature-gated; default builds skip it.
    pub async fn set_window_rect(&self, rect: HostWindowRect) {
        if let Some(session) = self.inner.read().await.as_ref() {
            session.set_window_rect(rect).await;
        }
        *self.pending_rect.lock().await = Some(rect);
        #[cfg(all(feature = "cef", target_os = "macos"))]
        crate::cef::embed::host_view::set_frame(rect);
    }

    /// Subscribe to state transitions. Each subscriber sees the
    /// current state immediately, then every transition in order.
    pub fn subscribe(&self) -> watch::Receiver<HostState> {
        self.state_tx.subscribe()
    }

    pub fn state(&self) -> HostState {
        self.state_tx.borrow().clone()
    }

    /// Attach this `CefHost` to the active in-process CEF engine.
    /// Reads `DevToolsActivePort` from the per-Thalyn profile that
    /// `cef::initialize` was configured against, surfaces the WS
    /// URL via [`HostState::Running`], and stores the [`CefSession`]
    /// for the renderer's rect plumbing to consume.
    ///
    /// Must be called only after
    /// [`crate::cef::embed::runtime::initialize_cef_engine`] has
    /// returned successfully — otherwise `DevToolsActivePort` will
    /// never be written and the watcher will time out. The setup
    /// hook calls `initialize_cef_engine` synchronously and then
    /// spawns an async task that calls this method, so the
    /// ordering holds by construction.
    pub async fn attach_to_active_engine(&self) -> Result<HostState, HostError> {
        // Gate the engine-initialised check on `feature = "cef"`.
        // Default builds compile this method (so the AppState
        // surface is uniform across feature configs) but the
        // engine is never reachable; the `cfg!` arm below makes
        // that explicit.
        #[cfg(feature = "cef")]
        let engine_initialized = crate::cef::embed::runtime::is_engine_initialized();
        #[cfg(not(feature = "cef"))]
        let engine_initialized = false;

        if !engine_initialized {
            let _ = self.state_tx.send(HostState::Exited {
                reason: "CEF engine was not initialised; the helper-bundle \
                         layout under `<App>.app/Contents/Frameworks/` is \
                         missing or `cef::initialize` failed."
                    .to_owned(),
            });
            return Err(HostError::EngineNotInitialized);
        }

        {
            let guard = self.inner.read().await;
            if guard.is_some() {
                return Err(HostError::AlreadyAttached);
            }
        }

        let profile = CefProfile::open(&self.profile_root)?;

        let _ = self.state_tx.send(HostState::Starting {
            profile_dir: profile.dir().display().to_string(),
        });

        let port_file = profile.port_file();
        // The engine is in-process and `cef::initialize` has
        // already returned; the port file is either present or
        // about to be. `wait_for_port_file` polls with the same
        // backoff the v0.29 child-binary path used. The
        // `is_alive` predicate is trivially true here because we
        // are no longer supervising a separate process.
        let endpoint = wait_for_port_file(&port_file, || true).await?;

        let pending_rect = *self.pending_rect.lock().await;
        let session = CefSession {
            profile,
            endpoint,
            window_rect: Arc::new(Mutex::new(pending_rect)),
        };

        let ws_url = session.ws_url();
        let profile_dir = session.profile_dir().display().to_string();

        *self.inner.write().await = Some(session);

        let state = HostState::Running {
            ws_url,
            profile_dir,
            sdk_version: super::pinned_cef_version().to_owned(),
        };
        let _ = self.state_tx.send(state.clone());
        Ok(state)
    }

    /// Renderer-driven engine start. In v0.30's in-process model
    /// the engine is initialised at app startup (Tauri setup
    /// hook), so this Tauri command is effectively a state
    /// re-fetch — it returns the current [`HostState`] without
    /// touching the engine. The renderer's "Start engine" button
    /// remains wired to this command for compatibility; future
    /// work that adds a multi-tab Browser UI will reshape it to
    /// `create_browser`.
    pub async fn start(&self) -> Result<HostState, HostError> {
        Ok(self.state())
    }

    /// Renderer-driven engine stop. Like [`Self::start`], a no-op
    /// in the in-process model — `cef::shutdown` is process-global
    /// and runs at app exit, not on demand. Future work that
    /// adds multi-tab Browser UI will reshape this to
    /// `close_browser`.
    pub async fn stop(&self) -> Result<(), HostError> {
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    fn temp_root(label: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "thalyn-cef-host-test-{}-{}-{:p}",
            label,
            std::process::id(),
            &label as *const _,
        ));
        if dir.exists() {
            std::fs::remove_dir_all(&dir).unwrap();
        }
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[tokio::test]
    async fn host_starts_in_idle_state() {
        let host = CefHost::new(temp_root("idle"));
        assert!(matches!(host.state(), HostState::Idle));
    }

    #[tokio::test]
    async fn pending_rect_is_held_until_a_session_starts() {
        let host = CefHost::new(temp_root("rect-pending"));
        let rect = HostWindowRect {
            x: 12.0,
            y: 84.0,
            width: 640.0,
            height: 480.0,
        };
        host.set_window_rect(rect).await;
        assert_eq!(*host.pending_rect.lock().await, Some(rect));
    }

    #[tokio::test]
    async fn start_when_idle_returns_idle_state() {
        // In the in-process model, start() is a no-op state
        // re-fetch. With no engine attached, it returns Idle.
        let host = CefHost::new(temp_root("start-idle"));
        let state = host.start().await.expect("start should not error");
        assert!(matches!(state, HostState::Idle));
    }

    #[tokio::test]
    async fn stop_is_a_noop() {
        let host = CefHost::new(temp_root("stop-noop"));
        host.stop().await.expect("stop should not error");
        assert!(matches!(host.state(), HostState::Idle));
    }

    #[tokio::test]
    async fn attach_without_engine_initialized_is_a_typed_error() {
        // The engine-init guard fires before any I/O; `cargo test`
        // never runs `cef::initialize`, so this path is the
        // baseline assertion. The `state_tx` lands on `Exited` so
        // the renderer surfaces the failure mode immediately.
        let host = CefHost::new(temp_root("attach-no-engine"));
        let err = host
            .attach_to_active_engine()
            .await
            .expect_err("attach should fail without an initialised engine");
        assert!(matches!(err, HostError::EngineNotInitialized));
        assert!(matches!(host.state(), HostState::Exited { .. }));
    }
}
