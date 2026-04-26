//! Brain sidecar supervisor.
//!
//! Spawns the Python sidecar as a child process and exchanges
//! NDJSON-framed JSON-RPC 2.0 messages with it over stdin/stdout.
//! Supports two call shapes:
//!
//! * [`BrainSupervisor::call`] — request/response, used for `ping`,
//!   provider listing, and other one-shots.
//! * [`BrainSupervisor::call_streaming`] — request that may emit
//!   notifications (with no `id`) before its final response;
//!   used for chat token streaming.
//!
//! A persistent reader task decodes envelopes off stdout and routes
//! them: responses go to a per-id oneshot channel, notifications go to
//! a broadcast channel that any number of listeners can subscribe to.
//! That lets LSP-style out-of-band notifications keep flowing even
//! while another request is in flight, and lets the Tauri layer
//! forward server-pushed events without holding open a streaming call.

use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::Duration;

use serde::{Deserialize, Serialize};
use serde_json::Value;
use thiserror::Error;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::process::{Child, ChildStdin, Command};
use tokio::sync::{broadcast, oneshot, Mutex};
use tokio::task::JoinHandle;
use tokio::time::timeout;

/// JSON-RPC 2.0 wire types.
#[derive(Debug, Serialize)]
struct RpcRequest<'a> {
    jsonrpc: &'a str,
    id: u64,
    method: &'a str,
    params: Value,
}

#[derive(Debug, Deserialize)]
struct RpcEnvelope {
    #[allow(dead_code)]
    jsonrpc: Option<String>,
    id: Option<u64>,
    method: Option<String>,
    params: Option<Value>,
    result: Option<Value>,
    error: Option<RpcError>,
}

#[derive(Debug, Deserialize, Serialize, Clone)]
pub struct RpcError {
    pub code: i64,
    pub message: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub data: Option<Value>,
}

/// Errors surfaced by the supervisor to its callers.
#[derive(Debug, Error)]
pub enum BrainError {
    #[error("failed to spawn brain sidecar: {0}")]
    Spawn(#[source] std::io::Error),
    #[error("brain sidecar exited before answering")]
    Eof,
    #[error("io error talking to brain sidecar: {0}")]
    Io(#[from] std::io::Error),
    #[error("brain sidecar response was not valid JSON: {0}")]
    Decode(#[from] serde_json::Error),
    #[error("brain sidecar returned an error: {0:?}")]
    Rpc(RpcError),
    #[error("brain sidecar took too long to answer")]
    Timeout,
}

type ResponseSlot = oneshot::Sender<Result<Value, RpcError>>;
type Pending = Arc<Mutex<HashMap<u64, ResponseSlot>>>;

const NOTIFICATION_BUFFER: usize = 256;

/// Long-lived handle to a running brain sidecar process.
pub struct BrainSupervisor {
    next_id: AtomicU64,
    stdin: Mutex<ChildStdin>,
    pending: Pending,
    notifications: broadcast::Sender<Notification>,
    child: Mutex<Child>,
    _reader: JoinHandle<()>,
}

/// One server-initiated notification — JSON-RPC `method` and
/// `params`. Cloned to every subscriber, so the params payload is
/// shared via `Arc` rather than re-serialised.
#[derive(Debug, Clone)]
pub struct Notification {
    pub method: Arc<str>,
    pub params: Arc<Value>,
}

/// Configuration for spawning the sidecar.
pub struct SpawnConfig {
    pub program: String,
    pub args: Vec<String>,
    pub working_dir: Option<PathBuf>,
    /// Environment variables to set on the spawned process. Used to
    /// forward API keys read from the OS keychain into the brain
    /// without writing them to disk.
    pub env: HashMap<String, String>,
}

impl SpawnConfig {
    /// Default development configuration: run the sidecar via uv from the
    /// in-tree `brain/` directory. We invoke `uv run python -m
    /// thalyn_brain` rather than the console-script entry point because
    /// the latter is wrapped in a thin .pth-dependent shim that Python
    /// 3.13 quietly ignores when uv installs in editable mode (see
    /// brain/README.md). Production configurations will replace this
    /// shape with a packaged binary discovered via Tauri's resource
    /// paths.
    pub fn dev_default() -> Self {
        let working_dir = std::env::current_dir()
            .ok()
            .and_then(|cwd| cwd.parent().map(PathBuf::from))
            .map(|root| root.join("brain"));
        Self {
            program: "uv".into(),
            args: vec![
                "run".into(),
                "python".into(),
                "-m".into(),
                "thalyn_brain".into(),
            ],
            working_dir,
            env: HashMap::new(),
        }
    }

    /// Override an environment variable for the spawn.
    pub fn with_env(mut self, key: impl Into<String>, value: impl Into<String>) -> Self {
        self.env.insert(key.into(), value.into());
        self
    }
}

impl BrainSupervisor {
    /// Spawn a sidecar process, capture its stdio, and start the
    /// persistent reader task.
    pub async fn spawn(config: SpawnConfig) -> Result<Self, BrainError> {
        let mut command = Command::new(&config.program);
        command
            .args(&config.args)
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::inherit())
            .kill_on_drop(true);
        if let Some(dir) = &config.working_dir {
            command.current_dir(dir);
        }
        for (key, value) in &config.env {
            command.env(key, value);
        }

        let mut child = command.spawn().map_err(BrainError::Spawn)?;
        let stdin = child.stdin.take().ok_or_else(|| {
            BrainError::Spawn(std::io::Error::other("brain sidecar exposed no stdin"))
        })?;
        let stdout = child.stdout.take().ok_or_else(|| {
            BrainError::Spawn(std::io::Error::other("brain sidecar exposed no stdout"))
        })?;

        let pending: Pending = Arc::new(Mutex::new(HashMap::new()));
        let (notifications, _) = broadcast::channel(NOTIFICATION_BUFFER);
        let reader_pending = pending.clone();
        let reader_notify = notifications.clone();
        let reader = tokio::spawn(reader_loop(
            BufReader::new(stdout),
            reader_pending,
            reader_notify,
        ));

        Ok(Self {
            next_id: AtomicU64::new(1),
            stdin: Mutex::new(stdin),
            pending,
            notifications,
            child: Mutex::new(child),
            _reader: reader,
        })
    }

    /// Send a JSON-RPC request and wait for the matching response.
    pub async fn call(
        &self,
        method: &str,
        params: Value,
        deadline: Duration,
    ) -> Result<Value, BrainError> {
        self.call_streaming(method, params, deadline, |_, _| {})
            .await
    }

    /// Send a JSON-RPC request, invoking ``on_notification`` for every
    /// notification the brain emits while the request is in flight,
    /// then return the final response.
    pub async fn call_streaming<F>(
        &self,
        method: &str,
        params: Value,
        deadline: Duration,
        mut on_notification: F,
    ) -> Result<Value, BrainError>
    where
        F: FnMut(&str, &Value),
    {
        let id = self.next_id.fetch_add(1, Ordering::Relaxed);
        let request = serde_json::to_vec(&RpcRequest {
            jsonrpc: "2.0",
            id,
            method,
            params,
        })?;

        let (tx, rx) = oneshot::channel();
        self.pending.lock().await.insert(id, tx);

        let mut subscriber = self.notifications.subscribe();

        // Write request bytes — stdin is the only point of contention
        // between concurrent callers.
        {
            let mut stdin = self.stdin.lock().await;
            stdin.write_all(&request).await?;
            stdin.write_all(b"\n").await?;
            stdin.flush().await?;
        }

        // Race: the response future, the notification stream, and the
        // deadline — whichever fires first wins. Notifications reset
        // the deadline so a long-running streaming call (chat) can
        // keep going as long as the brain is making progress.
        let result = tokio::select! {
            biased;
            response = consume_response(rx, deadline, &mut subscriber, &mut on_notification) => response,
        };

        if result.is_err() {
            self.pending.lock().await.remove(&id);
        }
        result
    }

    /// Subscribe to every server-initiated notification. Use this to
    /// forward LSP, terminal, or other long-lived event streams to
    /// the renderer without holding open a streaming RPC call.
    #[allow(dead_code)]
    pub fn subscribe_notifications(&self) -> broadcast::Receiver<Notification> {
        self.notifications.subscribe()
    }

    /// Best-effort shutdown: drop stdin (signals EOF to the sidecar) and
    /// wait briefly for the process to exit.
    #[allow(dead_code)]
    pub async fn shutdown(self) {
        // Drop stdin; the reader will see EOF and exit on its own.
        let mut child = self.child.into_inner();
        let _ = timeout(Duration::from_secs(2), child.wait()).await;
    }
}

/// Reader task: pull lines off the brain's stdout, decode them, and
/// route responses to the per-id oneshot, notifications to the
/// broadcast channel.
async fn reader_loop(
    mut stdout: BufReader<tokio::process::ChildStdout>,
    pending: Pending,
    notifications: broadcast::Sender<Notification>,
) {
    loop {
        let mut buf = String::new();
        match stdout.read_line(&mut buf).await {
            Ok(0) => break,
            Ok(_) => {}
            Err(err) => {
                tracing::warn!(?err, "brain reader: stdout read failed");
                break;
            }
        }

        let envelope: RpcEnvelope = match serde_json::from_str(buf.trim_end()) {
            Ok(env) => env,
            Err(err) => {
                tracing::warn!(?err, line = %buf.trim_end(), "brain reader: invalid envelope");
                continue;
            }
        };

        if let Some(id) = envelope.id {
            let slot = pending.lock().await.remove(&id);
            let Some(slot) = slot else {
                tracing::warn!(id, "brain reader: response for unknown request id");
                continue;
            };
            let result = if let Some(err) = envelope.error {
                Err(err)
            } else {
                Ok(envelope.result.unwrap_or(Value::Null))
            };
            let _ = slot.send(result);
            continue;
        }

        if let Some(method) = envelope.method {
            let params = envelope.params.unwrap_or(Value::Null);
            let _ = notifications.send(Notification {
                method: Arc::from(method),
                params: Arc::new(params),
            });
        }
    }

    // EOF — drain any pending callers with an Eof error so they
    // don't hang forever.
    let mut pending = pending.lock().await;
    for (_, slot) in pending.drain() {
        let _ = slot.send(Err(RpcError {
            code: -1,
            message: "brain sidecar EOF".into(),
            data: None,
        }));
    }
}

async fn consume_response<F>(
    mut rx: oneshot::Receiver<Result<Value, RpcError>>,
    deadline: Duration,
    subscriber: &mut broadcast::Receiver<Notification>,
    on_notification: &mut F,
) -> Result<Value, BrainError>
where
    F: FnMut(&str, &Value),
{
    loop {
        tokio::select! {
            biased;
            result = &mut rx => {
                return match result {
                    Ok(Ok(v)) => Ok(v),
                    Ok(Err(err)) if err.code == -1 && err.message == "brain sidecar EOF" => {
                        Err(BrainError::Eof)
                    }
                    Ok(Err(err)) => Err(BrainError::Rpc(err)),
                    Err(_) => Err(BrainError::Eof),
                };
            }
            recv = subscriber.recv() => {
                match recv {
                    Ok(notification) => {
                        on_notification(notification.method.as_ref(), notification.params.as_ref());
                    }
                    Err(broadcast::error::RecvError::Lagged(_)) => {
                        // Subscriber fell behind — drop the gap and keep going.
                    }
                    Err(broadcast::error::RecvError::Closed) => {
                        return Err(BrainError::Eof);
                    }
                }
            }
            _ = tokio::time::sleep(deadline) => {
                return Err(BrainError::Timeout);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::Path;

    fn brain_dir() -> PathBuf {
        let manifest = Path::new(env!("CARGO_MANIFEST_DIR"));
        manifest
            .parent()
            .expect("crate has a parent dir")
            .join("brain")
    }

    fn dev_config() -> SpawnConfig {
        SpawnConfig {
            program: "uv".into(),
            args: vec![
                "run".into(),
                "python".into(),
                "-m".into(),
                "thalyn_brain".into(),
            ],
            working_dir: Some(brain_dir()),
            env: HashMap::new(),
        }
    }

    /// Round-trip a real ping against a real Python sidecar.
    #[tokio::test]
    async fn pings_real_sidecar() {
        let supervisor = match BrainSupervisor::spawn(dev_config()).await {
            Ok(s) => s,
            Err(BrainError::Spawn(err)) => {
                eprintln!("skipping: failed to spawn sidecar ({err})");
                return;
            }
            Err(other) => panic!("unexpected spawn error: {other}"),
        };
        let result = supervisor
            .call("ping", serde_json::json!({}), Duration::from_secs(20))
            .await
            .expect("ping should succeed");
        assert_eq!(result["pong"], serde_json::Value::Bool(true));
        supervisor.shutdown().await;
    }
}
