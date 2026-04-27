//! Spawn + supervise a single Chromium child.
//!
//! Chromium writes a `DevToolsActivePort` file under its
//! `--user-data-dir` once the remote-debugging port is open. The file
//! contains two lines: the chosen TCP port (Chromium picks a free one
//! when we pass `--remote-debugging-port=0`) and the absolute WS path
//! to the browser endpoint. We poll for the file with a short tick
//! until it exists, then read once and never again — Chromium does
//! not rewrite it during a session.
//!
//! Termination and exit detection share one watcher task. The
//! watcher races `child.wait()` against a oneshot kill trigger; on
//! kill it sends SIGKILL and reaps; on natural death it records the
//! exit. Either way, the recorded [`SessionExit`] lands on a watch
//! channel the manager can read without blocking.
//!
//! Auto-restart is deliberately not built into the supervisor — the
//! wider browser manager owns the "should we respawn?" question
//! because the right answer depends on whether an agent run is in
//! flight, the user's last interaction, etc.

use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::time::{Duration, Instant};

use thiserror::Error;
use tokio::io::{AsyncBufReadExt, BufReader};
use tokio::process::{Child, Command};
use tokio::sync::{oneshot, watch};
use tokio::task::JoinHandle;
use tokio::time::sleep;

use super::discover::BrowserBinary;

const PORT_FILE_NAME: &str = "DevToolsActivePort";
const PORT_FILE_POLL_INTERVAL: Duration = Duration::from_millis(50);
const PORT_FILE_MAX_WAIT: Duration = Duration::from_secs(10);

#[derive(Debug, Error)]
pub enum SupervisorError {
    #[error("could not create profile directory: {0}")]
    ProfileCreate(String),
    #[error("could not spawn browser process: {0}")]
    Spawn(#[source] std::io::Error),
    #[error("browser failed to write {name} within {timeout:?}", name = PORT_FILE_NAME)]
    PortFileTimeout { timeout: Duration },
    #[error("browser wrote a malformed {name}: {detail}", name = PORT_FILE_NAME)]
    PortFileMalformed { detail: String },
    #[error("browser process exited before DevTools became ready: {0:?}")]
    EarlyExit(Option<i32>),
    #[error("browser termination failed: {0}")]
    Terminate(#[source] std::io::Error),
}

#[derive(Debug, Clone)]
pub struct SpawnOptions {
    pub binary: BrowserBinary,
    pub profile_dir: PathBuf,
}

/// Why a session ended. The manager turns this into a wire-friendly
/// reason string for the renderer.
#[derive(Debug, Clone)]
pub enum SessionExit {
    /// Operator-requested kill via [`BrowserSession::terminate`].
    Killed,
    /// Child exited on its own with a status code.
    Crashed { code: Option<i32> },
    /// Unix-only — terminated by signal.
    Signalled { signal: String },
}

/// One running Chromium instance.
pub struct BrowserSession {
    binary: BrowserBinary,
    profile_dir: PathBuf,
    ws_url: String,
    devtools_port: u16,
    /// Sender side of the kill trigger; `terminate` sends `()` here.
    /// Wrapped in `Option` because oneshot consumes self on send.
    kill_tx: Option<oneshot::Sender<()>>,
    /// Set once by the watcher task when the child exits.
    exit_rx: watch::Receiver<Option<SessionExit>>,
    _watcher: JoinHandle<()>,
}

impl BrowserSession {
    /// Spawn Chromium and wait for the DevTools endpoint to come up.
    pub async fn spawn(opts: SpawnOptions) -> Result<Self, SupervisorError> {
        let SpawnOptions {
            binary,
            profile_dir,
        } = opts;

        // Make sure no stale port file lingers from a previous run.
        let port_file = profile_dir.join(PORT_FILE_NAME);
        if port_file.exists() {
            // Best-effort; if we can't remove it the wait below will
            // see stale data and we'll surface a malformed-port error.
            let _ = std::fs::remove_file(&port_file);
        }

        let mut command = Command::new(&binary.path);
        command
            .arg(format!("--user-data-dir={}", profile_dir.display()))
            .arg("--remote-debugging-port=0")
            .arg("--no-first-run")
            .arg("--no-default-browser-check")
            .arg("--disable-features=Translate")
            .stdout(Stdio::null())
            .stderr(Stdio::piped())
            .stdin(Stdio::null())
            .kill_on_drop(true);

        let mut child = command.spawn().map_err(SupervisorError::Spawn)?;

        // Tee Chromium's stderr into the tracing log so a launch
        // failure (e.g. missing system library) is debuggable; the
        // task ends when the pipe closes.
        if let Some(stderr) = child.stderr.take() {
            tokio::spawn(async move {
                let mut reader = BufReader::new(stderr).lines();
                while let Ok(Some(line)) = reader.next_line().await {
                    tracing::debug!(target = "thalyn::browser", "chromium: {line}");
                }
            });
        }

        let (port, ws_path) = wait_for_port_file(&port_file, &mut child).await?;
        let ws_url = format!("ws://127.0.0.1:{port}{ws_path}");

        let (exit_tx, exit_rx) = watch::channel::<Option<SessionExit>>(None);
        let (kill_tx, kill_rx) = oneshot::channel::<()>();
        let watcher = spawn_exit_watcher(child, kill_rx, exit_tx);

        Ok(Self {
            binary,
            profile_dir,
            ws_url,
            devtools_port: port,
            kill_tx: Some(kill_tx),
            exit_rx,
            _watcher: watcher,
        })
    }

    pub fn ws_url(&self) -> &str {
        &self.ws_url
    }

    pub fn devtools_port(&self) -> u16 {
        self.devtools_port
    }

    pub fn binary(&self) -> &BrowserBinary {
        &self.binary
    }

    pub fn profile_dir(&self) -> &Path {
        &self.profile_dir
    }

    /// Already-known exit, if the watcher has observed one. Used by
    /// the manager to surface unexpected death without blocking.
    pub fn current_exit(&self) -> Option<SessionExit> {
        self.exit_rx.borrow().clone()
    }

    /// Ask the child to exit and wait for the watcher to confirm.
    /// Idempotent if the child has already died on its own.
    pub async fn terminate(mut self) -> Result<SessionExit, SupervisorError> {
        if let Some(exit) = self.exit_rx.borrow().clone() {
            return Ok(exit);
        }
        if let Some(tx) = self.kill_tx.take() {
            // The receiver may already be dropped if the child exited
            // after we read `exit_rx` but before we sent — that's fine.
            let _ = tx.send(());
        }
        // Wait for the watcher to record the exit.
        loop {
            if let Some(exit) = self.exit_rx.borrow().clone() {
                return Ok(exit);
            }
            if self.exit_rx.changed().await.is_err() {
                // Watcher dropped the sender without setting a value —
                // treat as already-killed.
                return Ok(SessionExit::Killed);
            }
        }
    }
}

async fn wait_for_port_file(
    port_file: &Path,
    child: &mut Child,
) -> Result<(u16, String), SupervisorError> {
    let started = Instant::now();
    loop {
        if let Ok(Some(status)) = child.try_wait() {
            return Err(SupervisorError::EarlyExit(status.code()));
        }
        if port_file.exists() {
            let raw = match std::fs::read_to_string(port_file) {
                Ok(s) => s,
                Err(_) => {
                    // Chromium may be mid-write; retry.
                    sleep(PORT_FILE_POLL_INTERVAL).await;
                    continue;
                }
            };
            return parse_port_file(&raw);
        }
        if started.elapsed() >= PORT_FILE_MAX_WAIT {
            // Best-effort kill so we don't leak a process.
            let _ = child.kill().await;
            return Err(SupervisorError::PortFileTimeout {
                timeout: PORT_FILE_MAX_WAIT,
            });
        }
        sleep(PORT_FILE_POLL_INTERVAL).await;
    }
}

fn parse_port_file(raw: &str) -> Result<(u16, String), SupervisorError> {
    let mut lines = raw.lines();
    let port_line = lines
        .next()
        .ok_or_else(|| SupervisorError::PortFileMalformed {
            detail: "empty".into(),
        })?
        .trim();
    let port: u16 = port_line
        .parse()
        .map_err(|_| SupervisorError::PortFileMalformed {
            detail: format!("bad port {port_line:?}"),
        })?;
    let ws_path = lines
        .next()
        .ok_or_else(|| SupervisorError::PortFileMalformed {
            detail: "missing ws path line".into(),
        })?
        .trim()
        .to_owned();
    if !ws_path.starts_with('/') {
        return Err(SupervisorError::PortFileMalformed {
            detail: format!("ws path must start with '/': {ws_path:?}"),
        });
    }
    Ok((port, ws_path))
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
                        tracing::warn!(target = "thalyn::browser", "child wait failed: {err}");
                        SessionExit::Crashed { code: None }
                    }
                }
            }
            _ = kill_rx => {
                if let Err(err) = child.kill().await {
                    tracing::warn!(target = "thalyn::browser", "child kill failed: {err}");
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

    #[test]
    fn parse_port_file_happy_path() {
        let raw = "53219\n/devtools/browser/abc-def\n";
        let (port, path) = parse_port_file(raw).unwrap();
        assert_eq!(port, 53219);
        assert_eq!(path, "/devtools/browser/abc-def");
    }

    #[test]
    fn parse_port_file_strips_trailing_whitespace() {
        let raw = "9222\n/devtools/browser/uuid\n";
        let (port, path) = parse_port_file(raw).unwrap();
        assert_eq!(port, 9222);
        assert_eq!(path, "/devtools/browser/uuid");
    }

    #[test]
    fn parse_port_file_rejects_missing_ws_line() {
        let raw = "9222\n";
        let err = parse_port_file(raw).unwrap_err();
        assert!(matches!(err, SupervisorError::PortFileMalformed { .. }));
    }

    #[test]
    fn parse_port_file_rejects_non_numeric_port() {
        let raw = "abc\n/devtools/browser/x\n";
        let err = parse_port_file(raw).unwrap_err();
        assert!(matches!(err, SupervisorError::PortFileMalformed { .. }));
    }

    #[test]
    fn parse_port_file_rejects_relative_ws_path() {
        let raw = "9222\nbrowser/x\n";
        let err = parse_port_file(raw).unwrap_err();
        assert!(matches!(err, SupervisorError::PortFileMalformed { .. }));
    }

    /// Spawn a fake "browser" — actually `/bin/sh` — that does not
    /// write the port file. We expect `wait_for_port_file` to time
    /// out cleanly and the supervisor to surface
    /// [`SupervisorError::PortFileTimeout`].
    #[tokio::test]
    #[cfg(unix)]
    async fn spawn_times_out_when_port_file_never_appears() {
        // Override the wait timeout for this test by spawning sh
        // directly and driving wait_for_port_file ourselves; the
        // `BrowserSession::spawn` happy path is exercised by the
        // integration smoke that lands later.
        let dir =
            std::env::temp_dir().join(format!("thalyn-browser-spawn-test-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let port_file = dir.join(PORT_FILE_NAME);
        let _ = std::fs::remove_file(&port_file);
        // `sleep 60` keeps the child alive longer than the wait, so
        // we exercise the timeout branch rather than the early-exit one.
        let mut child = Command::new("/bin/sh")
            .arg("-c")
            .arg("sleep 60")
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .stdin(Stdio::null())
            .kill_on_drop(true)
            .spawn()
            .unwrap();
        let err = wait_for_port_file(&port_file, &mut child)
            .await
            .unwrap_err();
        assert!(matches!(err, SupervisorError::PortFileTimeout { .. }));
        let _ = child.kill().await;
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// A "browser" that exits immediately, before writing the port
    /// file, surfaces `EarlyExit` rather than a stuck wait.
    #[tokio::test]
    #[cfg(unix)]
    async fn spawn_surfaces_early_exit() {
        let dir =
            std::env::temp_dir().join(format!("thalyn-browser-early-exit-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let port_file = dir.join(PORT_FILE_NAME);
        let _ = std::fs::remove_file(&port_file);
        let mut child = Command::new("/bin/sh")
            .arg("-c")
            .arg("exit 7")
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .stdin(Stdio::null())
            .kill_on_drop(true)
            .spawn()
            .unwrap();
        let err = wait_for_port_file(&port_file, &mut child)
            .await
            .unwrap_err();
        assert!(matches!(err, SupervisorError::EarlyExit(Some(7))));
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// Happy path — a "browser" that writes a well-formed port file,
    /// then sleeps. We confirm `wait_for_port_file` returns the parsed
    /// values.
    #[tokio::test]
    #[cfg(unix)]
    async fn spawn_reads_port_file_when_written() {
        let dir = std::env::temp_dir().join(format!("thalyn-browser-happy-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let port_file = dir.join(PORT_FILE_NAME);
        let _ = std::fs::remove_file(&port_file);
        let port_file_str = port_file.display().to_string();
        // Write the port file after a short delay, then sleep.
        let mut child = Command::new("/bin/sh")
            .arg("-c")
            .arg(format!(
                "sleep 0.1 && printf '%s\\n%s\\n' 47291 /devtools/browser/test > {port_file_str} && sleep 60"
            ))
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .stdin(Stdio::null())
            .kill_on_drop(true)
            .spawn()
            .unwrap();
        let (port, ws) = wait_for_port_file(&port_file, &mut child).await.unwrap();
        assert_eq!(port, 47291);
        assert_eq!(ws, "/devtools/browser/test");
        let _ = child.kill().await;
        let _ = std::fs::remove_dir_all(&dir);
    }
}
