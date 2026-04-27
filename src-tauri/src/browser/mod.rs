//! Headed Chromium sidecar for agent browsing.
//!
//! The brain orchestrates browser-driving agents through CDP over a
//! WebSocket; the headed Chromium window is the user-facing browser
//! surface (logins, file uploads, downloads, IME, drag-drop, DRM all
//! just work). The Rust core owns the process lifecycle: it discovers
//! a Chromium-family binary on the user's machine, spawns it with a
//! per-Thalyn profile, watches the `DevToolsActivePort` file the
//! browser writes on startup, and surfaces the resulting WS URL so
//! the brain can attach. ADR-0010 covers the wider sidecar decision;
//! the spike at `docs/spikes/2026-04-26-webview-chromium-reparenting.md`
//! is why the panel is observability-only and the real window stays
//! the user-facing surface.
//!
//! ## Threats and scope
//!
//! v1 single-user-on-own-laptop scope per `01-requirements.md` §10.1
//! OQ-2. We defend against runaway agents (the browser session is
//! confined to its own Chromium profile, so cookies don't bleed into
//! the user's main browser); we do not defend against a hostile site
//! attacking the local machine, which is the user's regular browser
//! threat model.
//!
//! ## Lifecycle
//!
//! [`BrowserManager`] owns at most one running [`BrowserSession`]. The
//! caller asks for a session; the manager discovers a binary, spawns
//! it, polls for the port file, and returns metadata containing the
//! WS URL. Stopping a session sends `SIGTERM` (or the platform
//! equivalent) and reaps the child. A separate watchdog task awaits
//! the child's exit and surfaces unexpected death — auto-restart is
//! out of scope for the first commit; the manager exposes the death
//! event so a higher layer can decide.

#![allow(dead_code)]

mod discover;
mod supervisor;

#[allow(unused_imports)]
pub use discover::{BrowserBinary, BrowserFamily, DiscoverError};
pub use supervisor::{BrowserSession, SessionExit, SpawnOptions, SupervisorError};

use std::path::PathBuf;
use std::sync::Arc;

use thiserror::Error;
use tokio::sync::{watch, RwLock};

/// Wire-friendly state the renderer can poll to render the panel.
#[derive(Debug, Clone, serde::Serialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum BrowserState {
    /// No session running.
    Idle,
    /// A session is starting; binary chosen, child spawned, port file
    /// not yet readable.
    Starting { binary: String },
    /// A session is running and the WS URL is available.
    Running {
        binary: String,
        ws_url: String,
        profile_dir: String,
    },
    /// The previous session exited; the manager is idle but the
    /// renderer can read why.
    Exited { reason: String },
}

#[derive(Debug, Error)]
pub enum BrowserError {
    #[error("no Chromium-family browser found on this machine: {0}")]
    Discover(#[from] DiscoverError),
    #[error("failed to spawn browser: {0}")]
    Supervisor(#[from] SupervisorError),
    #[error("a browser session is already running")]
    AlreadyRunning,
    #[error("no browser session is running")]
    NotRunning,
}

/// Single-session manager. Plumbed through `AppState`; commands and
/// notifications land in subsequent commits.
pub struct BrowserManager {
    profile_root: PathBuf,
    inner: Arc<RwLock<Option<BrowserSession>>>,
    state_tx: watch::Sender<BrowserState>,
    /// Held so consumers can subscribe even before a session starts.
    _state_rx: watch::Receiver<BrowserState>,
}

impl BrowserManager {
    /// Build a new manager. The `profile_root` is the directory under
    /// which the per-Thalyn Chromium profile lives; on first session
    /// start the manager creates `<profile_root>/chromium-profile/`.
    pub fn new(profile_root: PathBuf) -> Self {
        let (tx, rx) = watch::channel(BrowserState::Idle);
        Self {
            profile_root,
            inner: Arc::new(RwLock::new(None)),
            state_tx: tx,
            _state_rx: rx,
        }
    }

    /// Subscribe to state transitions. Each subscriber sees the
    /// current state immediately, then every transition in order.
    pub fn subscribe(&self) -> watch::Receiver<BrowserState> {
        self.state_tx.subscribe()
    }

    /// Returns the current state without blocking.
    pub fn state(&self) -> BrowserState {
        self.state_tx.borrow().clone()
    }

    /// Spawn a new browser session. Returns `AlreadyRunning` if one
    /// is already live; the caller must `stop` first.
    pub async fn start(&self) -> Result<BrowserState, BrowserError> {
        {
            let guard = self.inner.read().await;
            if guard.is_some() {
                return Err(BrowserError::AlreadyRunning);
            }
        }
        let binary = discover::find_browser()?;
        let profile_dir = self.profile_root.join("chromium-profile");
        std::fs::create_dir_all(&profile_dir)
            .map_err(|e| BrowserError::Supervisor(SupervisorError::ProfileCreate(e.to_string())))?;
        let display_path = binary.path.display().to_string();
        let _ = self.state_tx.send(BrowserState::Starting {
            binary: display_path.clone(),
        });

        let opts = SpawnOptions {
            binary: binary.clone(),
            profile_dir: profile_dir.clone(),
        };
        let session = match BrowserSession::spawn(opts).await {
            Ok(s) => s,
            Err(err) => {
                let _ = self.state_tx.send(BrowserState::Exited {
                    reason: err.to_string(),
                });
                return Err(BrowserError::Supervisor(err));
            }
        };

        let running = BrowserState::Running {
            binary: display_path,
            ws_url: session.ws_url().to_owned(),
            profile_dir: profile_dir.display().to_string(),
        };
        let _ = self.state_tx.send(running.clone());
        *self.inner.write().await = Some(session);
        Ok(running)
    }

    /// Stop the current session if any. Idempotent on `NotRunning`
    /// callers that don't want to care.
    pub async fn stop(&self) -> Result<(), BrowserError> {
        let session = self.inner.write().await.take();
        let session = session.ok_or(BrowserError::NotRunning)?;
        let exit = session.terminate().await?;
        let reason = match exit {
            SessionExit::Killed => "stopped by user".to_owned(),
            SessionExit::Crashed { code } => format!("crashed (exit code {code:?})"),
            SessionExit::Signalled { signal } => format!("signalled ({signal})"),
        };
        let _ = self.state_tx.send(BrowserState::Exited { reason });
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    fn temp_root(label: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "thalyn-browser-test-{}-{}",
            label,
            std::process::id()
        ));
        if dir.exists() {
            std::fs::remove_dir_all(&dir).unwrap();
        }
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[tokio::test]
    async fn manager_starts_in_idle_state() {
        let mgr = BrowserManager::new(temp_root("idle"));
        assert!(matches!(mgr.state(), BrowserState::Idle));
    }

    #[tokio::test]
    async fn stop_when_idle_is_an_error() {
        let mgr = BrowserManager::new(temp_root("stop-idle"));
        let err = mgr.stop().await.unwrap_err();
        assert!(matches!(err, BrowserError::NotRunning));
    }
}
