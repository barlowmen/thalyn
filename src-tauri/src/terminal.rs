//! Terminal sessions backed by portable-pty.
//!
//! Each [`TerminalSession`] owns a pseudo-terminal pair, a child
//! shell process, and a reader thread that streams stdout/stderr
//! bytes back to the renderer as `terminal:data` events. Inputs flow
//! the other way through [`TerminalManager::write`]; resize events
//! reach the slave through [`TerminalManager::resize`].
//!
//! The manager is designed for both the renderer (TerminalPane in
//! the editor surface) and the agent-attach tool (next commit) to
//! share without contention. Output is fan-out: the manager keeps a
//! ring buffer of the last few KB of bytes per session so a late
//! attaching agent can replay the most recent context.
//!
//! Sessions are identified by a `term_<uuid>` string assigned at
//! creation; lifecycle ownership is the renderer's, but anyone with
//! the id can subscribe to its output.

use std::collections::{HashMap, VecDeque};
use std::io::{Read, Write};
use std::sync::Arc;
use std::time::Duration;

use portable_pty::{native_pty_system, CommandBuilder, MasterPty, PtySize};
use serde::Serialize;
use thiserror::Error;
use tokio::sync::{broadcast, Mutex};

const RECENT_BUFFER_BYTES: usize = 16 * 1024;
const SUBSCRIBER_CAPACITY: usize = 256;

/// Errors surfaced by the manager.
#[derive(Debug, Error)]
pub enum TerminalError {
    #[error("unknown terminal session: {0}")]
    UnknownSession(String),
    #[error("failed to open pty: {0}")]
    OpenPty(String),
    #[error("failed to spawn shell: {0}")]
    SpawnShell(String),
    #[error("failed to write to terminal: {0}")]
    Write(#[source] std::io::Error),
    #[error("failed to clone pty handle: {0}")]
    CloneHandle(String),
    #[error("failed to resize terminal: {0}")]
    Resize(String),
}

/// One frame of bytes from a terminal's output. `seq` is monotonic
/// per session so subscribers can replay deterministically.
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TerminalChunk {
    pub session_id: String,
    pub seq: u64,
    pub data: String,
}

/// A spawned terminal — owns the pty, the child process, and the
/// reader-side bookkeeping.
struct TerminalSession {
    session_id: String,
    master: Mutex<Box<dyn MasterPty + Send>>,
    writer: Mutex<Box<dyn Write + Send>>,
    broadcaster: broadcast::Sender<TerminalChunk>,
    recent: Mutex<VecDeque<u8>>,
    seq: Mutex<u64>,
    /// Kept so the child is reaped when the session is dropped.
    _child: Mutex<Box<dyn portable_pty::Child + Send + Sync>>,
}

/// Public manager — Tauri commands talk to this.
pub struct TerminalManager {
    sessions: Mutex<HashMap<String, Arc<TerminalSession>>>,
}

impl Default for TerminalManager {
    fn default() -> Self {
        Self::new()
    }
}

impl TerminalManager {
    pub fn new() -> Self {
        Self {
            sessions: Mutex::new(HashMap::new()),
        }
    }

    /// Spawn a fresh shell-backed terminal. Returns the session id;
    /// callers should immediately subscribe via [`TerminalManager::subscribe`]
    /// to start receiving output.
    pub async fn open(
        &self,
        program: Option<String>,
        cwd: Option<std::path::PathBuf>,
        cols: u16,
        rows: u16,
    ) -> Result<String, TerminalError> {
        let pty_system = native_pty_system();
        let pair = pty_system
            .openpty(PtySize {
                rows,
                cols,
                pixel_width: 0,
                pixel_height: 0,
            })
            .map_err(|err| TerminalError::OpenPty(err.to_string()))?;

        let shell = program.unwrap_or_else(default_shell);
        let mut command = CommandBuilder::new(&shell);
        command.env("TERM", "xterm-256color");
        // Make sure the child inherits a sane PATH; portable-pty's
        // CommandBuilder otherwise starts from a clean env.
        if let Ok(path) = std::env::var("PATH") {
            command.env("PATH", path);
        }
        if let Ok(home) = std::env::var("HOME") {
            command.env("HOME", home);
        }
        if let Some(dir) = cwd {
            command.cwd(dir);
        }

        let child = pair
            .slave
            .spawn_command(command)
            .map_err(|err| TerminalError::SpawnShell(err.to_string()))?;
        // The slave handle is no longer needed once the child is
        // running — drop it so we don't hold the pty open longer than
        // we need to.
        drop(pair.slave);

        let writer = pair
            .master
            .take_writer()
            .map_err(|err| TerminalError::CloneHandle(err.to_string()))?;
        let reader = pair
            .master
            .try_clone_reader()
            .map_err(|err| TerminalError::CloneHandle(err.to_string()))?;

        let session_id = format!("term_{}", uuid::Uuid::new_v4().simple());
        let (tx, _rx) = broadcast::channel(SUBSCRIBER_CAPACITY);

        let session = Arc::new(TerminalSession {
            session_id: session_id.clone(),
            master: Mutex::new(pair.master),
            writer: Mutex::new(writer),
            broadcaster: tx,
            recent: Mutex::new(VecDeque::with_capacity(RECENT_BUFFER_BYTES)),
            seq: Mutex::new(0),
            _child: Mutex::new(child),
        });

        spawn_reader(session.clone(), reader);

        self.sessions
            .lock()
            .await
            .insert(session_id.clone(), session);
        Ok(session_id)
    }

    /// Subscribe to the byte stream for `session_id`. Returns a
    /// receiver plus the recent buffer so a late subscriber can
    /// replay the last RECENT_BUFFER_BYTES bytes of context.
    pub async fn subscribe(
        &self,
        session_id: &str,
    ) -> Result<(broadcast::Receiver<TerminalChunk>, String), TerminalError> {
        let session = self
            .sessions
            .lock()
            .await
            .get(session_id)
            .cloned()
            .ok_or_else(|| TerminalError::UnknownSession(session_id.into()))?;
        let recent = session.recent.lock().await;
        let bytes: Vec<u8> = recent.iter().copied().collect();
        let snapshot = String::from_utf8_lossy(&bytes).into_owned();
        Ok((session.broadcaster.subscribe(), snapshot))
    }

    pub async fn write(&self, session_id: &str, data: &[u8]) -> Result<(), TerminalError> {
        let session = self
            .sessions
            .lock()
            .await
            .get(session_id)
            .cloned()
            .ok_or_else(|| TerminalError::UnknownSession(session_id.into()))?;
        let mut writer = session.writer.lock().await;
        writer.write_all(data).map_err(TerminalError::Write)?;
        writer.flush().map_err(TerminalError::Write)?;
        Ok(())
    }

    pub async fn resize(
        &self,
        session_id: &str,
        cols: u16,
        rows: u16,
    ) -> Result<(), TerminalError> {
        let session = self
            .sessions
            .lock()
            .await
            .get(session_id)
            .cloned()
            .ok_or_else(|| TerminalError::UnknownSession(session_id.into()))?;
        let master = session.master.lock().await;
        master
            .resize(PtySize {
                rows,
                cols,
                pixel_width: 0,
                pixel_height: 0,
            })
            .map_err(|err| TerminalError::Resize(err.to_string()))
    }

    pub async fn close(&self, session_id: &str) -> Result<bool, TerminalError> {
        let session = self.sessions.lock().await.remove(session_id);
        match session {
            Some(s) => {
                // Best-effort kill: terminate the child cleanly. The
                // reader thread will exit on EOF once the pty closes.
                let mut child = s._child.lock().await;
                let _ = child.kill();
                Ok(true)
            }
            None => Ok(false),
        }
    }

    /// Snapshot for tests / introspection: which sessions are open
    /// right now, in creation order.
    pub async fn list(&self) -> Vec<String> {
        let sessions = self.sessions.lock().await;
        let mut out: Vec<String> = sessions.keys().cloned().collect();
        out.sort();
        out
    }
}

fn default_shell() -> String {
    if let Ok(s) = std::env::var("SHELL") {
        if !s.is_empty() {
            return s;
        }
    }
    if cfg!(target_os = "windows") {
        "cmd.exe".into()
    } else {
        "/bin/sh".into()
    }
}

/// Background blocking thread that pumps pty output into the
/// broadcast channel. Lives on a dedicated OS thread because pty
/// reads are blocking — driving them from tokio's reactor would gum
/// up the runtime.
fn spawn_reader(session: Arc<TerminalSession>, mut reader: Box<dyn Read + Send>) {
    std::thread::spawn(move || {
        let mut buf = [0u8; 4096];
        loop {
            match reader.read(&mut buf) {
                Ok(0) => break,
                Ok(n) => {
                    let data = String::from_utf8_lossy(&buf[..n]).into_owned();
                    let chunk = TerminalChunk {
                        session_id: session.session_id.clone(),
                        seq: increment_seq(&session),
                        data,
                    };
                    push_recent(&session, &buf[..n]);
                    let _ = session.broadcaster.send(chunk);
                }
                Err(err) => {
                    if err.kind() == std::io::ErrorKind::WouldBlock {
                        std::thread::sleep(Duration::from_millis(10));
                        continue;
                    }
                    tracing::debug!(?err, "pty reader exiting");
                    break;
                }
            }
        }
    });
}

fn increment_seq(session: &TerminalSession) -> u64 {
    let mut guard = session.seq.blocking_lock();
    *guard += 1;
    *guard
}

fn push_recent(session: &TerminalSession, data: &[u8]) {
    let mut guard = session.recent.blocking_lock();
    for byte in data {
        if guard.len() >= RECENT_BUFFER_BYTES {
            guard.pop_front();
        }
        guard.push_back(*byte);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn open_then_list_then_close_round_trip() {
        let manager = TerminalManager::new();
        let id = manager
            .open(Some("/bin/sh".into()), None, 80, 24)
            .await
            .expect("open");
        let listed = manager.list().await;
        assert!(listed.contains(&id));
        assert!(manager.close(&id).await.expect("close"));
        let listed_after = manager.list().await;
        assert!(!listed_after.contains(&id));
    }

    #[tokio::test]
    async fn write_then_subscribe_sees_echo() {
        let manager = TerminalManager::new();
        let id = manager
            .open(Some("/bin/sh".into()), None, 80, 24)
            .await
            .expect("open");
        let (mut rx, _snapshot) = manager.subscribe(&id).await.expect("subscribe");

        manager
            .write(&id, b"echo terminal-ok\n")
            .await
            .expect("write");

        // Wait for the shell to echo the command + the result.
        let mut all = String::new();
        let deadline = std::time::Instant::now() + Duration::from_secs(5);
        while std::time::Instant::now() < deadline {
            match tokio::time::timeout(Duration::from_millis(500), rx.recv()).await {
                Ok(Ok(chunk)) => {
                    all.push_str(&chunk.data);
                    if all.contains("terminal-ok\n") {
                        break;
                    }
                }
                Ok(Err(_)) | Err(_) => continue,
            }
        }
        assert!(
            all.contains("terminal-ok"),
            "expected echo to include terminal-ok; got: {all:?}"
        );

        manager.close(&id).await.expect("close");
    }

    #[tokio::test]
    async fn close_unknown_session_returns_false() {
        let manager = TerminalManager::new();
        assert!(!manager.close("term_nope").await.expect("close"));
    }
}
