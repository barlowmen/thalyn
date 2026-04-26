//! System power assertions — keep the system awake during runs.
//!
//! The user-visible behaviour: the display can sleep during an
//! agent run; the system cannot. We acquire a per-run power
//! assertion when a brain notification reports `run.status =
//! running`, and release it on a terminal status.
//!
//! Implementation by platform:
//!
//! - **macOS:** spawn `caffeinate -i` (IOPMAssertionCreate under
//!   the hood). The child process holds the assertion until
//!   killed; release just kills it.
//! - **Linux:** spawn `systemd-inhibit --what=idle sleep infinity`.
//!   Same lifecycle.
//! - **Windows:** *not yet implemented* — release returns Ok so a
//!   shipping Mac/Linux build doesn't error out, and acquire
//!   returns a token that's a no-op on release.
//!
//! The native APIs (`IOPMAssertionCreateWithName`,
//! `SetThreadExecutionState`) are the right long-term path; the
//! subprocess approach lands the right user-visible behaviour now
//! without pulling in platform-specific FFI crates that the
//! packaging story will have to chase later.

#![allow(dead_code)]

use std::collections::HashMap;
use std::process::Stdio;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;

use thiserror::Error;
use tokio::process::{Child, Command};
use tokio::sync::Mutex;

#[derive(Debug, Error)]
pub enum PowerError {
    #[error("power assertion not supported on this platform")]
    Unsupported,
    #[error("failed to acquire power assertion: {0}")]
    Acquire(String),
}

/// Token that uniquely identifies an outstanding power assertion.
/// Pass this back to ``release`` to drop the underlying lock.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct AssertionToken(u64);

#[derive(Default)]
pub struct PowerManager {
    inner: Arc<Mutex<HashMap<AssertionToken, AssertionHandle>>>,
    next_id: Arc<AtomicU64>,
}

struct AssertionHandle {
    /// Reason captured at acquire time, surfaced for diagnostics.
    reason: String,
    /// On platforms where we hold the assertion through a child
    /// process (macOS / Linux today), this is the child we kill on
    /// release. `None` on platforms where acquire is a no-op.
    child: Option<Child>,
}

impl PowerManager {
    pub fn new() -> Self {
        Self::default()
    }

    /// Acquire a system-wake power assertion. The token returned is
    /// the only handle that releases it; dropping the manager
    /// without releasing leaves the underlying child running until
    /// the parent exits.
    pub async fn acquire(&self, reason: impl Into<String>) -> Result<AssertionToken, PowerError> {
        let reason = reason.into();
        let child = spawn_assertion(&reason)?;
        let id = AssertionToken(self.next_id.fetch_add(1, Ordering::Relaxed));
        let handle = AssertionHandle { reason, child };
        self.inner.lock().await.insert(id, handle);
        Ok(id)
    }

    /// Release a previously-acquired assertion. Releasing an
    /// unknown token is a silent no-op so callers don't need to
    /// track whether they've already released.
    pub async fn release(&self, token: AssertionToken) {
        let mut map = self.inner.lock().await;
        if let Some(mut handle) = map.remove(&token) {
            if let Some(mut child) = handle.child.take() {
                let _ = child.kill().await;
                let _ = child.wait().await;
            }
            drop(handle);
        }
    }

    /// Test-only / diagnostic — how many assertions are currently held.
    pub async fn outstanding(&self) -> usize {
        self.inner.lock().await.len()
    }
}

#[cfg(target_os = "macos")]
fn spawn_assertion(reason: &str) -> Result<Option<Child>, PowerError> {
    // `-i` blocks idle sleep, `-D` keeps the display awake too —
    // we use `-i` only so the display can still sleep per spec.
    Command::new("caffeinate")
        .arg("-i")
        // Pass the reason via the deprecated `-r` flag so it shows
        // up in `pmset -g assertions`. Older caffeinate on macOS
        // ignores -r; we use a no-op label instead.
        .arg("-r")
        .arg(reason)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map(Some)
        .map_err(|err| PowerError::Acquire(format!("caffeinate spawn failed: {err}")))
}

#[cfg(target_os = "linux")]
fn spawn_assertion(reason: &str) -> Result<Option<Child>, PowerError> {
    // ``systemd-inhibit`` holds the inhibitor for as long as the
    // command it wraps runs. ``sleep infinity`` parks until killed.
    Command::new("systemd-inhibit")
        .arg("--what=idle")
        .arg("--why")
        .arg(reason)
        .arg("sleep")
        .arg("infinity")
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map(Some)
        .map_err(|err| PowerError::Acquire(format!("systemd-inhibit spawn failed: {err}")))
}

#[cfg(target_os = "windows")]
fn spawn_assertion(_reason: &str) -> Result<Option<Child>, PowerError> {
    // Windows path lands when SetThreadExecutionState wires in.
    // Returning ``None`` keeps acquire / release behavioural parity
    // for callers; the assertion just doesn't pin the system.
    Ok(None)
}

#[cfg(not(any(target_os = "macos", target_os = "linux", target_os = "windows")))]
fn spawn_assertion(_reason: &str) -> Result<Option<Child>, PowerError> {
    Err(PowerError::Unsupported)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn acquire_and_release_round_trip() {
        let manager = PowerManager::new();
        let token = match manager.acquire("test").await {
            Ok(token) => token,
            Err(PowerError::Acquire(_)) | Err(PowerError::Unsupported) => {
                eprintln!("skipping: power assertion not available");
                return;
            }
        };
        assert_eq!(manager.outstanding().await, 1);
        manager.release(token).await;
        assert_eq!(manager.outstanding().await, 0);
    }

    #[tokio::test]
    async fn release_of_unknown_token_is_a_silent_noop() {
        let manager = PowerManager::new();
        manager.release(AssertionToken(42)).await;
        assert_eq!(manager.outstanding().await, 0);
    }

    #[tokio::test]
    async fn multiple_assertions_track_independently() {
        let manager = PowerManager::new();
        let a = match manager.acquire("a").await {
            Ok(token) => token,
            Err(_) => return,
        };
        let b = match manager.acquire("b").await {
            Ok(token) => token,
            Err(_) => {
                manager.release(a).await;
                return;
            }
        };
        assert_eq!(manager.outstanding().await, 2);
        assert_ne!(a, b);
        manager.release(a).await;
        assert_eq!(manager.outstanding().await, 1);
        manager.release(b).await;
        assert_eq!(manager.outstanding().await, 0);
    }
}
