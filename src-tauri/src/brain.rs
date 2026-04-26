//! Brain sidecar supervisor.
//!
//! Spawns the Python sidecar as a child process and exchanges
//! NDJSON-framed JSON-RPC 2.0 messages with it over stdin/stdout. The
//! walking-skeleton surface is a single `ping` request; concurrent
//! in-flight requests, streaming notifications, and a richer transport
//! (Unix domain socket / Windows named pipe) all land in subsequent
//! iterations of the runtime.

use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Duration;

use serde::{Deserialize, Serialize};
use serde_json::Value;
use thiserror::Error;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::process::{Child, ChildStdin, Command};
use tokio::sync::Mutex;
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
struct RpcResponse {
    #[allow(dead_code)]
    jsonrpc: Option<String>,
    id: Option<u64>,
    result: Option<Value>,
    error: Option<RpcError>,
}

#[derive(Debug, Deserialize, Serialize)]
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
    #[error("brain sidecar response had no result")]
    EmptyResult,
    #[error("brain sidecar took too long to answer")]
    Timeout,
}

/// Long-lived handle to a running brain sidecar process.
///
/// Cheap to clone — the underlying state is held behind an `Arc`-style
/// `Mutex` so multiple call sites can serialise their requests against
/// the single duplex stream.
pub struct BrainSupervisor {
    next_id: AtomicU64,
    inner: Mutex<Inner>,
}

struct Inner {
    // Held to keep `kill_on_drop` semantics in scope; `shutdown` consumes it.
    child: Child,
    stdin: ChildStdin,
    stdout: BufReader<tokio::process::ChildStdout>,
}

/// Configuration for spawning the sidecar.
pub struct SpawnConfig {
    pub program: String,
    pub args: Vec<String>,
    pub working_dir: Option<PathBuf>,
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
        }
    }
}

impl BrainSupervisor {
    /// Spawn a sidecar process and capture its stdio.
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

        let mut child = command.spawn().map_err(BrainError::Spawn)?;
        let stdin = child.stdin.take().ok_or_else(|| {
            BrainError::Spawn(std::io::Error::other("brain sidecar exposed no stdin"))
        })?;
        let stdout = child.stdout.take().ok_or_else(|| {
            BrainError::Spawn(std::io::Error::other("brain sidecar exposed no stdout"))
        })?;

        Ok(Self {
            next_id: AtomicU64::new(1),
            inner: Mutex::new(Inner {
                child,
                stdin,
                stdout: BufReader::new(stdout),
            }),
        })
    }

    /// Send a JSON-RPC request and wait for the matching response.
    ///
    /// Concurrent callers serialise on the inner mutex — this is fine for
    /// the walking-skeleton surface where all traffic is request/response.
    /// A multiplexing layer with id correlation can replace this when
    /// streaming notifications come online.
    pub async fn call(
        &self,
        method: &str,
        params: Value,
        deadline: Duration,
    ) -> Result<Value, BrainError> {
        let id = self.next_id.fetch_add(1, Ordering::Relaxed);
        let request = serde_json::to_vec(&RpcRequest {
            jsonrpc: "2.0",
            id,
            method,
            params,
        })?;

        let mut inner = self.inner.lock().await;
        inner.stdin.write_all(&request).await?;
        inner.stdin.write_all(b"\n").await?;
        inner.stdin.flush().await?;

        let mut buf = String::new();
        let read = timeout(deadline, inner.stdout.read_line(&mut buf))
            .await
            .map_err(|_| BrainError::Timeout)??;
        if read == 0 {
            return Err(BrainError::Eof);
        }

        let response: RpcResponse = serde_json::from_str(buf.trim_end())?;
        if let Some(err) = response.error {
            return Err(BrainError::Rpc(err));
        }
        if response.id != Some(id) {
            tracing::warn!(
                expected = id,
                actual = ?response.id,
                "brain response id did not match request id",
            );
        }
        response.result.ok_or(BrainError::EmptyResult)
    }

    /// Best-effort shutdown: drop stdin (signals EOF to the sidecar) and
    /// wait briefly for the process to exit.
    #[allow(dead_code)]
    pub async fn shutdown(self) {
        let mut inner = self.inner.into_inner();
        drop(inner.stdin);
        let _ = timeout(Duration::from_secs(2), inner.child.wait()).await;
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
        }
    }

    /// Round-trip a real ping against a real Python sidecar.
    ///
    /// Skipped automatically when `uv` is not on PATH or the brain venv
    /// has not been synced — that keeps the suite green on a fresh
    /// checkout where the developer hasn't run `uv sync` yet.
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
