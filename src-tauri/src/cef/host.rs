//! CEF lifecycle owner — public API surface that the rest of the
//! engine consumes.
//!
//! Per ADR-0019's 2026-04-30 refinement, v0.29 ships CEF in a child
//! binary (`thalyn-cef-host`) parented to the Tauri main window via
//! OS child-window APIs. This module spawns that binary, threads
//! the per-Thalyn profile dir through, watches `DevToolsActivePort`
//! for the WebSocket URL, and supervises the child's lifecycle. The
//! brain attaches to the WS URL over CDP — the engine swap is
//! invisible to the brain except for the new in-app URL.
//!
//! Process management mirrors the v1 system-Chromium supervisor's
//! shape: a single watcher task races `child.wait()` against a
//! oneshot kill trigger; on kill it sends SIGKILL and reaps; on
//! natural death it records the exit. The recorded [`SessionExit`]
//! lands on a watch channel the manager can read without blocking.
//!
//! The child binary's location is discovered via either the
//! `THALYN_CEF_HOST_BIN` environment override (used in tests and
//! dev hot-swap) or the standard "next to `current_exe`" sibling
//! lookup. Bundle-shaped layout (helper apps under
//! `<App>.app/Contents/Frameworks/`) lands with the macOS bundle
//! work, not here.

use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::sync::Arc;

use thiserror::Error;
use tokio::io::{AsyncBufReadExt, BufReader};
use tokio::process::{Child, Command};
use tokio::sync::{oneshot, watch, Mutex, RwLock};
use tokio::task::JoinHandle;

use super::port_file::{wait_for_port_file, DevToolsEndpoint, PortFileError};
use super::profile::{CefProfile, ProfileError};
use super::sdk::{CefSdk, SdkResolveError};

/// Default basename of the bundled child binary. Cargo's
/// `[[bin]] name = "thalyn-cef-host"` produces this filename in
/// `target/<profile>/`. Windows builds gain the `.exe` suffix; the
/// resolver below adds it as needed.
const CHILD_BINARY_BASENAME: &str = "thalyn-cef-host";

/// Environment variable that forces a specific child-binary path.
/// Used by tests to point at a synthetic stand-in, and by
/// developers to hot-swap a separately-built binary without
/// reinstalling the app bundle.
const CHILD_BINARY_ENV: &str = "THALYN_CEF_HOST_BIN";

/// Drawer-host rectangle the renderer reports via
/// `cef_set_window_rect`. Coordinates are in CSS pixels relative to
/// the Tauri main window's content view (which equal macOS points at
/// the typical Retina devicePixelRatio of 2). The OS-specific
/// parenting layer converts this to its native frame as needed —
/// macOS `NSWindow.addChildWindow:`, Windows `SetParent`, X11
/// `XReparentWindow`. Stored on [`CefSession`] so the parenting
/// layer always has the latest rect, and so a session that starts
/// after the renderer has already pushed a rect can apply it
/// immediately.
#[derive(Debug, Clone, Copy, PartialEq, serde::Deserialize, serde::Serialize)]
pub struct HostWindowRect {
    pub x: f64,
    pub y: f64,
    pub width: f64,
    pub height: f64,
}

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
    #[error("could not locate the thalyn-cef-host binary: {0}")]
    ChildBinaryMissing(String),
    #[error("could not spawn thalyn-cef-host: {0}")]
    Spawn(#[source] std::io::Error),
    #[error("thalyn-cef-host exited with code {code:?} before the DevTools endpoint became ready")]
    ChildExitedEarly { code: Option<i32> },
    #[error("a CEF session is already running")]
    AlreadyRunning,
    #[error("no CEF session is running")]
    NotRunning,
}

/// Why the child exited. Mirrors the v1 supervisor's shape so the
/// renderer's reason-string mapping carries forward unchanged.
#[derive(Debug, Clone)]
pub enum SessionExit {
    /// Operator-requested kill via [`CefHost::stop`].
    Killed,
    /// Child exited on its own.
    Crashed { code: Option<i32> },
    /// Unix-only — terminated by signal.
    Signalled { signal: String },
}

/// Handle to one live CEF session: the resolved SDK, the profile
/// dir, the DevTools endpoint the brain attaches to, and the
/// supervision plumbing for the child process. The watcher task is
/// held here so dropping the session terminates the child cleanly.
pub struct CefSession {
    sdk: CefSdk,
    profile: CefProfile,
    endpoint: DevToolsEndpoint,
    /// Sender side of the kill trigger; `terminate` sends `()` here.
    /// Wrapped in `Option` because oneshot consumes self on send.
    kill_tx: Option<oneshot::Sender<()>>,
    /// Set once by the watcher task when the child exits.
    exit_rx: watch::Receiver<Option<SessionExit>>,
    /// Latest drawer-host rect the renderer has reported, shared with
    /// the OS-specific parenting layer. Held in an `Arc<Mutex<...>>`
    /// so the parenting layer can read it without taking the
    /// host-level write lock.
    window_rect: Arc<Mutex<Option<HostWindowRect>>>,
    _watcher: JoinHandle<()>,
}

impl CefSession {
    pub fn ws_url(&self) -> String {
        self.endpoint.ws_url()
    }

    pub fn profile_dir(&self) -> &Path {
        self.profile.dir()
    }

    pub fn sdk(&self) -> &CefSdk {
        &self.sdk
    }

    pub fn endpoint(&self) -> &DevToolsEndpoint {
        &self.endpoint
    }

    /// Already-known exit, if the watcher has observed one. Used by
    /// the manager to surface unexpected death without blocking.
    pub fn current_exit(&self) -> Option<SessionExit> {
        self.exit_rx.borrow().clone()
    }

    /// Read the latest drawer-host rect the renderer has reported,
    /// if any. The OS-specific parenting layer consumes this when
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

    /// Ask the child to exit and wait for the watcher to confirm.
    /// Idempotent if the child has already died on its own.
    pub async fn terminate(mut self) -> SessionExit {
        if let Some(exit) = self.exit_rx.borrow().clone() {
            return exit;
        }
        if let Some(tx) = self.kill_tx.take() {
            // Receiver may already be dropped if the child exited
            // after we read `exit_rx` but before we sent — that's fine.
            let _ = tx.send(());
        }
        loop {
            if let Some(exit) = self.exit_rx.borrow().clone() {
                return exit;
            }
            if self.exit_rx.changed().await.is_err() {
                // Watcher dropped without setting a value — treat as
                // already-killed.
                return SessionExit::Killed;
            }
        }
    }
}

/// Owner of the CEF runtime. Single instance per app — CEF's
/// `initialize` / `shutdown` pair is process-global, so multiple
/// hosts in one process are illegal. The renderer drives lifecycle
/// through this surface; the brain is just a CDP consumer of the
/// resulting WS URL.
pub struct CefHost {
    profile_root: PathBuf,
    inner: RwLock<Option<CefSession>>,
    state_tx: watch::Sender<HostState>,
    /// Held so consumers can subscribe before any session starts.
    _state_rx: watch::Receiver<HostState>,
    /// Latest rect the renderer has pushed before a session existed.
    /// On `start`, the new session adopts this rect so the parenting
    /// layer can apply it immediately rather than waiting for the
    /// next renderer tick.
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

    /// Update the drawer-host rect. Routed from the
    /// `cef_set_window_rect` Tauri command. If a session is live the
    /// rect lands on it; otherwise it is held as the
    /// next-session pending rect so a fresh session adopts it
    /// without a renderer round-trip.
    pub async fn set_window_rect(&self, rect: HostWindowRect) {
        if let Some(session) = self.inner.read().await.as_ref() {
            session.set_window_rect(rect).await;
        }
        *self.pending_rect.lock().await = Some(rect);
    }

    /// Subscribe to state transitions. Each subscriber sees the
    /// current state immediately, then every transition in order.
    pub fn subscribe(&self) -> watch::Receiver<HostState> {
        self.state_tx.subscribe()
    }

    pub fn state(&self) -> HostState {
        self.state_tx.borrow().clone()
    }

    /// Spawn the child CEF binary and wait for the DevTools endpoint
    /// to come up. Returns the resulting `Running` state when the
    /// brain can attach.
    pub async fn start(&self) -> Result<HostState, HostError> {
        {
            let guard = self.inner.read().await;
            if guard.is_some() {
                return Err(HostError::AlreadyRunning);
            }
        }

        let sdk = CefSdk::resolve_default()?;
        let profile = CefProfile::open(&self.profile_root)?;
        profile.clear_stale_port_file()?;

        let _ = self.state_tx.send(HostState::Starting {
            profile_dir: profile.dir().display().to_string(),
        });

        let child_binary = locate_child_binary()?;
        let mut child = spawn_child(&child_binary, &sdk, &profile)?;

        // Tee stderr to tracing so a launch failure (missing system
        // library, helper bundle path mismatch, etc.) is debuggable.
        if let Some(stderr) = child.stderr.take() {
            tokio::spawn(async move {
                let mut reader = BufReader::new(stderr).lines();
                while let Ok(Some(line)) = reader.next_line().await {
                    tracing::debug!(target = "thalyn::cef", "thalyn-cef-host: {line}");
                }
            });
        }

        let port_file = profile.port_file();
        let endpoint_result = {
            let alive = || matches!(child.try_wait(), Ok(None));
            wait_for_port_file(&port_file, alive).await
        };
        let endpoint = match endpoint_result {
            Ok(endpoint) => endpoint,
            Err(err) => {
                // The child may already be dead; record the exit code
                // before reaping so the caller gets a typed error.
                let exit_code = match child.try_wait() {
                    Ok(Some(status)) => status.code(),
                    _ => None,
                };
                let _ = child.kill().await;
                return if let Some(code) = exit_code {
                    Err(HostError::ChildExitedEarly { code: Some(code) })
                } else {
                    Err(HostError::PortFile(err))
                };
            }
        };

        let (exit_tx, exit_rx) = watch::channel::<Option<SessionExit>>(None);
        let (kill_tx, kill_rx) = oneshot::channel::<()>();
        let watcher = spawn_exit_watcher(child, kill_rx, exit_tx);

        let ws_url = endpoint.ws_url();
        let pending_rect = *self.pending_rect.lock().await;
        let session = CefSession {
            sdk: sdk.clone(),
            profile: profile.clone(),
            endpoint,
            kill_tx: Some(kill_tx),
            exit_rx,
            window_rect: Arc::new(Mutex::new(pending_rect)),
            _watcher: watcher,
        };

        *self.inner.write().await = Some(session);

        let state = HostState::Running {
            ws_url,
            profile_dir: profile.dir().display().to_string(),
            sdk_version: sdk.version().to_owned(),
        };
        let _ = self.state_tx.send(state.clone());
        Ok(state)
    }

    /// Stop the running session, if any. Idempotent on `NotRunning`.
    pub async fn stop(&self) -> Result<(), HostError> {
        let session = self.inner.write().await.take();
        let session = session.ok_or(HostError::NotRunning)?;
        let exit = session.terminate().await;
        let reason = match exit {
            SessionExit::Killed => "stopped by user".to_owned(),
            SessionExit::Crashed { code } => format!("child exited with code {code:?}"),
            SessionExit::Signalled { signal } => format!("child killed by signal {signal}"),
        };
        let _ = self.state_tx.send(HostState::Exited { reason });
        Ok(())
    }
}

/// Resolve the child binary path. `THALYN_CEF_HOST_BIN` overrides
/// everything; otherwise we look next to `current_exe`. Bundle-style
/// layout (helper apps under `Contents/Frameworks/`) lands with the
/// macOS bundle work.
fn locate_child_binary() -> Result<PathBuf, HostError> {
    if let Some(value) = std::env::var_os(CHILD_BINARY_ENV) {
        let path = PathBuf::from(value);
        if path.exists() {
            return Ok(path);
        }
        return Err(HostError::ChildBinaryMissing(format!(
            "{CHILD_BINARY_ENV} points at {} but the file is missing",
            path.display()
        )));
    }
    let exe = std::env::current_exe()
        .map_err(|err| HostError::ChildBinaryMissing(format!("current_exe failed: {err}")))?;
    let parent = exe
        .parent()
        .ok_or_else(|| HostError::ChildBinaryMissing(format!("{} has no parent", exe.display())))?;
    let candidate = if cfg!(windows) {
        parent.join(format!("{CHILD_BINARY_BASENAME}.exe"))
    } else {
        parent.join(CHILD_BINARY_BASENAME)
    };
    if candidate.exists() {
        return Ok(candidate);
    }
    Err(HostError::ChildBinaryMissing(format!(
        "no thalyn-cef-host binary at {} and {CHILD_BINARY_ENV} is unset",
        candidate.display()
    )))
}

fn spawn_child(binary: &Path, sdk: &CefSdk, profile: &CefProfile) -> Result<Child, HostError> {
    let mut command = Command::new(binary);
    command
        .arg("--profile-dir")
        .arg(profile.dir())
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .stdin(Stdio::null())
        .kill_on_drop(true);

    // Linux loads `libcef.so` from `<sdk>/Release/`; the build script
    // uses an absolute link search path, but at runtime the dynamic
    // loader still needs to find the .so. Append rather than replace
    // so anything the parent already needed (Tauri's own bundled
    // libs, system Wayland/X11 paths) keeps working.
    #[cfg(target_os = "linux")]
    {
        let runtime_dir = sdk.runtime_library_dir();
        let combined = match std::env::var_os("LD_LIBRARY_PATH") {
            Some(existing) => {
                let mut v: Vec<PathBuf> = Vec::new();
                v.push(runtime_dir);
                for path in std::env::split_paths(&existing) {
                    v.push(path);
                }
                std::env::join_paths(v)
                    .map_err(|err| HostError::Spawn(std::io::Error::other(err)))?
            }
            None => runtime_dir.into_os_string(),
        };
        command.env("LD_LIBRARY_PATH", combined);
    }

    // Windows: the loader walks `PATH` for `libcef.dll`. Same
    // append-front shape.
    #[cfg(target_os = "windows")]
    {
        let runtime_dir = sdk.runtime_library_dir();
        let combined = match std::env::var_os("PATH") {
            Some(existing) => {
                let mut v: Vec<PathBuf> = Vec::new();
                v.push(runtime_dir);
                for path in std::env::split_paths(&existing) {
                    v.push(path);
                }
                std::env::join_paths(v)
                    .map_err(|err| HostError::Spawn(std::io::Error::other(err)))?
            }
            None => runtime_dir.into_os_string(),
        };
        command.env("PATH", combined);
    }

    // macOS resolves the framework relative to the helper bundle
    // layout via the cef-rs library_loader. No env var to set; the
    // bundle structure is the contract.
    #[cfg(target_os = "macos")]
    {
        let _ = sdk; // referenced only to keep the param uniform.
    }

    command.spawn().map_err(HostError::Spawn)
}

fn spawn_exit_watcher(
    mut child: Child,
    kill_rx: oneshot::Receiver<()>,
    exit_tx: watch::Sender<Option<SessionExit>>,
) -> JoinHandle<()> {
    tokio::spawn(async move {
        let exit = tokio::select! {
            wait_result = child.wait() => {
                match wait_result {
                    Ok(status) => exit_from_status(status),
                    Err(err) => {
                        tracing::warn!(target = "thalyn::cef", "child wait failed: {err}");
                        SessionExit::Crashed { code: None }
                    }
                }
            }
            _ = kill_rx => {
                if let Err(err) = child.kill().await {
                    tracing::warn!(target = "thalyn::cef", "child kill failed: {err}");
                }
                let _ = child.wait().await;
                SessionExit::Killed
            }
        };
        let _ = exit_tx.send(Some(exit));
    })
}

fn exit_from_status(status: std::process::ExitStatus) -> SessionExit {
    if let Some(code) = status.code() {
        return SessionExit::Crashed { code: Some(code) };
    }
    #[cfg(unix)]
    {
        use std::os::unix::process::ExitStatusExt;
        if let Some(sig) = status.signal() {
            return SessionExit::Signalled {
                signal: format!("{sig}"),
            };
        }
    }
    SessionExit::Crashed { code: None }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::PermissionsExt;
    use std::sync::OnceLock;
    use tokio::sync::Mutex as AsyncMutex;

    /// Tests in this module mutate process-global env vars
    /// (`CEF_PATH`, `THALYN_CEF_HOST_BIN`) to point at synthetic
    /// fixtures. Cargo runs tests in parallel by default, which makes
    /// those mutations race; this lock serialises just the env-using
    /// tests without forcing `--test-threads=1` on the whole suite.
    /// `tokio::sync::Mutex` so the guard can be held across `await`
    /// boundaries (the env vars must stay set while `start` polls for
    /// the port file).
    fn env_lock() -> &'static AsyncMutex<()> {
        static LOCK: OnceLock<AsyncMutex<()>> = OnceLock::new();
        LOCK.get_or_init(|| AsyncMutex::new(()))
    }

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

    fn lay_out_synthetic_sdk(cef_path: &Path) {
        let version = super::super::sdk::pinned_cef_version();
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
        let sdk_dir = cef_path.join(version).join(os_arch);
        std::fs::create_dir_all(sdk_dir.join("Release")).unwrap();
        for name in ["libcef.so", "libcef.dll"] {
            std::fs::write(sdk_dir.join("Release").join(name), b"stub").unwrap();
        }
        let mac_framework = sdk_dir.join("Chromium Embedded Framework.framework");
        std::fs::create_dir_all(mac_framework.join("Libraries")).unwrap();
        std::fs::write(mac_framework.join("Chromium Embedded Framework"), b"stub").unwrap();
    }

    /// Write a sh script that mimics the child binary: parses
    /// `--profile-dir <path>`, writes a valid `DevToolsActivePort` to
    /// it, then sleeps so the parent can observe the running state.
    #[cfg(unix)]
    fn write_synthetic_child(dir: &Path, behaviour: &str) -> PathBuf {
        let script = dir.join("thalyn-cef-host");
        std::fs::write(
            &script,
            format!(
                "#!/bin/sh\n\
                 PROFILE_DIR=\n\
                 while [ $# -gt 0 ]; do\n\
                 case \"$1\" in\n\
                 --profile-dir) shift; PROFILE_DIR=\"$1\";;\n\
                 esac\n\
                 shift\n\
                 done\n\
                 {behaviour}\n"
            ),
        )
        .unwrap();
        let mut perms = std::fs::metadata(&script).unwrap().permissions();
        perms.set_mode(0o755);
        std::fs::set_permissions(&script, perms).unwrap();
        script
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
    async fn stop_when_idle_is_a_typed_error() {
        let host = CefHost::new(temp_root("stop-idle"));
        let err = host.stop().await.unwrap_err();
        assert!(matches!(err, HostError::NotRunning));
    }

    #[tokio::test]
    async fn start_surfaces_missing_child_binary() {
        let root = temp_root("missing-bin");
        let cef_path = root.join("cef-cache");
        lay_out_synthetic_sdk(&cef_path);
        let host = CefHost::new(root.join("profile-root"));

        let _guard = env_lock().lock().await;
        std::env::set_var("CEF_PATH", &cef_path);
        std::env::set_var(CHILD_BINARY_ENV, root.join("does-not-exist"));
        let result = host.start().await;
        std::env::remove_var(CHILD_BINARY_ENV);
        std::env::remove_var("CEF_PATH");
        drop(_guard);

        assert!(matches!(result, Err(HostError::ChildBinaryMissing(_))));
    }

    #[tokio::test]
    #[cfg(unix)]
    async fn start_returns_running_when_child_writes_port_file() {
        let root = temp_root("running");
        let cef_path = root.join("cef-cache");
        lay_out_synthetic_sdk(&cef_path);
        // Synthetic child: write a valid DevToolsActivePort then sleep
        // long enough for the parent to observe the Running state.
        let bin_dir = root.join("bin");
        std::fs::create_dir_all(&bin_dir).unwrap();
        let script = write_synthetic_child(
            &bin_dir,
            "printf '47291\\n/devtools/browser/test\\n' > \"$PROFILE_DIR/DevToolsActivePort\"\n\
             sleep 30",
        );

        let host = CefHost::new(root.join("profile-root"));
        let _guard = env_lock().lock().await;
        std::env::set_var("CEF_PATH", &cef_path);
        std::env::set_var(CHILD_BINARY_ENV, &script);
        let result = host.start().await;
        let stop_result = host.stop().await;
        std::env::remove_var(CHILD_BINARY_ENV);
        std::env::remove_var("CEF_PATH");
        drop(_guard);

        let state = result.expect("start should succeed");
        match state {
            HostState::Running {
                ws_url,
                profile_dir: _,
                sdk_version: _,
            } => {
                assert_eq!(ws_url, "ws://127.0.0.1:47291/devtools/browser/test");
            }
            other => panic!("expected Running, got {other:?}"),
        }
        stop_result.expect("stop should succeed");
    }

    #[tokio::test]
    #[cfg(unix)]
    async fn set_window_rect_lands_on_a_running_session() {
        let root = temp_root("rect-session");
        let cef_path = root.join("cef-cache");
        lay_out_synthetic_sdk(&cef_path);
        let bin_dir = root.join("bin");
        std::fs::create_dir_all(&bin_dir).unwrap();
        let script = write_synthetic_child(
            &bin_dir,
            "printf '47100\\n/devtools/browser/rect\\n' > \"$PROFILE_DIR/DevToolsActivePort\"\n\
             sleep 30",
        );

        let host = CefHost::new(root.join("profile-root"));
        let _guard = env_lock().lock().await;
        std::env::set_var("CEF_PATH", &cef_path);
        std::env::set_var(CHILD_BINARY_ENV, &script);

        host.start().await.expect("start should succeed");
        let rect = HostWindowRect {
            x: 4.0,
            y: 88.0,
            width: 720.0,
            height: 540.0,
        };
        host.set_window_rect(rect).await;

        let observed = {
            let guard = host.inner.read().await;
            guard
                .as_ref()
                .expect("session live")
                .current_window_rect()
                .await
        };

        let stop_result = host.stop().await;
        std::env::remove_var(CHILD_BINARY_ENV);
        std::env::remove_var("CEF_PATH");
        drop(_guard);

        assert_eq!(observed, Some(rect));
        stop_result.expect("stop should succeed");
    }

    #[tokio::test]
    #[cfg(unix)]
    async fn start_surfaces_early_exit_when_child_dies_before_port_file() {
        let root = temp_root("early-exit");
        let cef_path = root.join("cef-cache");
        lay_out_synthetic_sdk(&cef_path);
        let bin_dir = root.join("bin");
        std::fs::create_dir_all(&bin_dir).unwrap();
        let script = write_synthetic_child(&bin_dir, "exit 7");

        let host = CefHost::new(root.join("profile-root"));
        let _guard = env_lock().lock().await;
        std::env::set_var("CEF_PATH", &cef_path);
        std::env::set_var(CHILD_BINARY_ENV, &script);
        let result = host.start().await;
        std::env::remove_var(CHILD_BINARY_ENV);
        std::env::remove_var("CEF_PATH");
        drop(_guard);

        match result {
            Err(HostError::ChildExitedEarly { code: Some(7) }) => {}
            other => panic!("expected ChildExitedEarly(7), got {other:?}"),
        }
    }
}
