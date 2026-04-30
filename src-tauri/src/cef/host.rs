//! CEF lifecycle owner — public API surface that the rest of the
//! engine consumes.
//!
//! The actual `cef::initialize` / `run_message_loop` plumbing is not
//! wired here yet; CEF and Tauri both want to own the main thread on
//! macOS, and that integration is being scoped in a separate spike
//! (`docs/spikes/2026-04-29-cef-macos-message-loop.md` lands with
//! the spike commit). What this module provides today is the API
//! surface every consumer of the engine will use — `CefHost::start`
//! returns the WS URL the brain attaches to, the profile path, and
//! a session handle whose `terminate` cleanly shuts the engine down.
//! The `start` body returns [`HostError::NotInitialized`] until the
//! spike's chosen integration shape lands.
//!
//! Everything in this module is engine-agnostic: it speaks
//! [`CefSdk`], [`CefProfile`], and [`port_file::DevToolsEndpoint`].
//! When the spike lands, only `start` and `stop` change — the
//! types and method signatures are stable.

use std::path::PathBuf;

use thiserror::Error;
use tokio::sync::{watch, RwLock};

use super::port_file::{DevToolsEndpoint, PortFileError};
use super::profile::{CefProfile, ProfileError};
use super::sdk::{CefSdk, SdkResolveError};

/// Public state of the CEF engine. Mirrors v1's `BrowserState` shape
/// so the renderer can render a consistent panel through the
/// engine swap.
#[derive(Debug, Clone, serde::Serialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum HostState {
    /// Not running; no resources held.
    Idle,
    /// Starting; CEF runtime is initializing.
    Starting { profile_dir: String },
    /// Running; the brain can attach to `ws_url`.
    Running {
        ws_url: String,
        profile_dir: String,
        sdk_version: String,
    },
    /// The previous session ended; reason is human-readable.
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
    /// Returned by [`CefHost::start`] until the macOS message-loop
    /// integration spike lands. The lifecycle, the API surface, and
    /// the supporting infrastructure (SDK resolve, profile dir, port
    /// file watcher) are all in place — only the engine-init call is
    /// missing. Carry forward the typed error rather than panicking
    /// so the renderer can render a clean "not yet available" state.
    #[error("CEF engine init not yet wired (pending macOS message-loop spike)")]
    NotInitialized,
    #[error("a CEF session is already running")]
    AlreadyRunning,
    #[error("no CEF session is running")]
    NotRunning,
}

/// Handle to one live CEF session. The actual engine-side resources
/// (`cef::Browser`, the multi-process supervisor, the per-step
/// capture pipeline) hang off this struct as `Option<...>` fields
/// once the spike chooses an integration shape; today the struct
/// only carries the resolved metadata so the API surface is real.
#[allow(dead_code)]
pub struct CefSession {
    sdk: CefSdk,
    profile: CefProfile,
    endpoint: DevToolsEndpoint,
}

impl CefSession {
    pub fn ws_url(&self) -> String {
        self.endpoint.ws_url()
    }

    pub fn profile_dir(&self) -> &std::path::Path {
        self.profile.dir()
    }

    pub fn sdk(&self) -> &CefSdk {
        &self.sdk
    }
}

/// Owner of the CEF runtime. Single instance per app — CEF's
/// `initialize` / `shutdown` pair is process-global, so multiple
/// hosts in one process are illegal. The renderer drives lifecycle
/// through this surface; the brain is just a CDP consumer of the
/// resulting WS URL.
#[allow(dead_code)]
pub struct CefHost {
    profile_root: PathBuf,
    inner: RwLock<Option<CefSession>>,
    state_tx: watch::Sender<HostState>,
    /// Held so consumers can subscribe before any session starts.
    _state_rx: watch::Receiver<HostState>,
}

impl CefHost {
    pub fn new(profile_root: PathBuf) -> Self {
        let (tx, rx) = watch::channel(HostState::Idle);
        Self {
            profile_root,
            inner: RwLock::new(None),
            state_tx: tx,
            _state_rx: rx,
        }
    }

    /// Subscribe to state transitions. Each subscriber sees the
    /// current state immediately, then every transition in order.
    pub fn subscribe(&self) -> watch::Receiver<HostState> {
        self.state_tx.subscribe()
    }

    pub fn state(&self) -> HostState {
        self.state_tx.borrow().clone()
    }

    /// Start the CEF engine and wait for the DevTools endpoint to
    /// come up.
    ///
    /// **Today:** returns [`HostError::NotInitialized`]. The
    /// supporting infrastructure runs (SDK resolve, profile open,
    /// stale port-file clear) so the failure mode the user sees is
    /// "engine not wired" rather than "missing SDK at runtime."
    /// When the macOS message-loop spike lands the call will
    /// continue past this point into the actual engine init.
    pub async fn start(&self) -> Result<HostState, HostError> {
        {
            let guard = self.inner.read().await;
            if guard.is_some() {
                return Err(HostError::AlreadyRunning);
            }
        }

        // Pre-flight resolution: we want resolution failures to
        // surface during start (not lazily on first browse) so the
        // renderer can render a clean error state. The SDK handle
        // is resolved here purely so the failure surfaces today; the
        // engine-init code that consumes it lands in step 3.B.
        let _sdk = CefSdk::resolve_default()?;
        let profile = CefProfile::open(&self.profile_root)?;
        profile.clear_stale_port_file()?;

        let _ = self.state_tx.send(HostState::Starting {
            profile_dir: profile.dir().display().to_string(),
        });

        // The actual engine init lands in step 3.B once the macOS
        // message-loop integration is decided. Surfacing a typed
        // error here lets the renderer show "browser engine not
        // available yet" without crashing.
        let _ = self.state_tx.send(HostState::Exited {
            reason: "CEF engine init pending message-loop spike".into(),
        });
        Err(HostError::NotInitialized)
    }

    /// Stop the running session, if any. Idempotent on `NotRunning`.
    pub async fn stop(&self) -> Result<(), HostError> {
        let session = self.inner.write().await.take();
        let _ = session.ok_or(HostError::NotRunning)?;
        let _ = self.state_tx.send(HostState::Exited {
            reason: "stopped by user".into(),
        });
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp_root(label: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "thalyn-cef-host-test-{}-{}",
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
    async fn host_starts_in_idle_state() {
        let host = CefHost::new(temp_root("idle"));
        assert!(matches!(host.state(), HostState::Idle));
    }

    #[tokio::test]
    async fn stop_when_idle_is_a_typed_error() {
        let host = CefHost::new(temp_root("stop-idle"));
        let err = host.stop().await.unwrap_err();
        assert!(matches!(err, HostError::NotRunning));
    }

    #[tokio::test]
    async fn start_returns_not_initialized_until_spike_lands() {
        // The supporting resolve/profile-open paths still need to
        // succeed for this assertion to hold, so set CEF_PATH to a
        // synthetic-but-valid SDK layout for the host's target.
        let root = temp_root("start-stub");
        let cef_path = root.join("cef-cache");
        let version = super::super::sdk::pinned_cef_version();
        let sdk_dir = sdk_dir_for_host(&cef_path, version);
        std::fs::create_dir_all(sdk_dir.join("Release")).unwrap();
        // libcef name varies by host OS; lay out all three so the
        // test passes regardless of which OS runs it.
        for name in ["libcef.so", "libcef.dll"] {
            std::fs::write(sdk_dir.join("Release").join(name), b"stub").unwrap();
        }
        let mac_framework = sdk_dir.join("Chromium Embedded Framework.framework");
        std::fs::create_dir_all(mac_framework.join("Libraries")).unwrap();
        std::fs::write(mac_framework.join("Chromium Embedded Framework"), b"stub").unwrap();

        let host = CefHost::new(root.join("profile-root"));
        std::env::set_var("CEF_PATH", &cef_path);
        let err = host.start().await.unwrap_err();
        std::env::remove_var("CEF_PATH");
        assert!(matches!(err, HostError::NotInitialized));

        // The exit reason should match the placeholder so a renderer
        // built against today's behaviour can pattern-match it.
        match host.state() {
            HostState::Exited { reason } => {
                assert!(reason.contains("pending"), "reason was: {reason}");
            }
            other => panic!("expected Exited, got {other:?}"),
        }
    }

    fn sdk_dir_for_host(cef_path: &std::path::Path, version: &str) -> PathBuf {
        let os_arch = if cfg!(all(target_os = "macos", target_arch = "aarch64")) {
            "cef_macos_aarch64"
        } else if cfg!(all(target_os = "macos", target_arch = "x86_64")) {
            "cef_macos_x86_64"
        } else if cfg!(all(target_os = "windows", target_arch = "x86_64")) {
            "cef_windows_x86_64"
        } else if cfg!(all(target_os = "linux", target_arch = "x86_64")) {
            "cef_linux_x86_64"
        } else if cfg!(all(target_os = "linux", target_arch = "aarch64")) {
            "cef_linux_aarch64"
        } else {
            "cef_unknown_unknown"
        };
        cef_path.join(version).join(os_arch)
    }
}
