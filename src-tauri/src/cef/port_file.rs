//! Parse and watch the `DevToolsActivePort` file CEF writes when
//! `--remote-debugging-port=0` picks an ephemeral port.
//!
//! Format (carried forward unchanged from upstream Chromium and
//! ADR-0010's v1 sidecar): two lines.
//!
//! ```text
//! 56123
//! /devtools/browser/9c8f4f1a-e5e2-4f0c-9d2a-1234abcd5678
//! ```
//!
//! Line 1 is the TCP port. Line 2 is the absolute WebSocket path to
//! the *browser* endpoint (vs a per-target endpoint). We compose
//! them into `ws://127.0.0.1:{port}{path}` and hand the URL to the
//! brain's CDP transport.
//!
//! The watcher loop polls at 50 ms and gives up after 10 s — long
//! enough for cold starts, short enough that a misconfigured engine
//! surfaces as an error rather than a hang. Both knobs match the v1
//! supervisor so behavior is unsurprising.

use std::path::Path;
use std::time::{Duration, Instant};

use thiserror::Error;
use tokio::time::sleep;

const POLL_INTERVAL: Duration = Duration::from_millis(50);
const MAX_WAIT: Duration = Duration::from_secs(10);

#[derive(Debug, Error)]
pub enum PortFileError {
    #[error("DevToolsActivePort never appeared at {path} within {timeout:?}")]
    Timeout {
        path: std::path::PathBuf,
        timeout: Duration,
    },
    #[error("DevToolsActivePort at {path} is malformed: {detail}")]
    Malformed {
        path: std::path::PathBuf,
        detail: String,
    },
}

/// Parsed contents of a `DevToolsActivePort` file.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DevToolsEndpoint {
    pub port: u16,
    pub ws_path: String,
}

impl DevToolsEndpoint {
    /// Compose the `ws://` URL the brain's CDP transport opens.
    pub fn ws_url(&self) -> String {
        format!("ws://127.0.0.1:{}{}", self.port, self.ws_path)
    }
}

/// Parse the two-line format. Whitespace-tolerant.
pub fn parse_port_file(raw: &str, path: &Path) -> Result<DevToolsEndpoint, PortFileError> {
    let mut lines = raw.lines();
    let port_line = lines.next().ok_or_else(|| PortFileError::Malformed {
        path: path.to_path_buf(),
        detail: "missing port line".into(),
    })?;
    let ws_path_line = lines.next().ok_or_else(|| PortFileError::Malformed {
        path: path.to_path_buf(),
        detail: "missing WS path line".into(),
    })?;

    let port = port_line
        .trim()
        .parse::<u16>()
        .map_err(|err| PortFileError::Malformed {
            path: path.to_path_buf(),
            detail: format!("port '{port_line}' is not a u16: {err}"),
        })?;
    let ws_path = ws_path_line.trim().to_owned();
    if !ws_path.starts_with('/') {
        return Err(PortFileError::Malformed {
            path: path.to_path_buf(),
            detail: format!("WS path '{ws_path}' does not start with '/'"),
        });
    }

    Ok(DevToolsEndpoint { port, ws_path })
}

/// Wait for `port_file` to appear, parse it, and return the endpoint.
/// `is_alive` is called between polls; if it returns `false` (the
/// CEF process died early, the user cancelled, etc.) we give up
/// promptly rather than waiting out the full timeout.
pub async fn wait_for_port_file<F>(
    port_file: &Path,
    mut is_alive: F,
) -> Result<DevToolsEndpoint, PortFileError>
where
    F: FnMut() -> bool,
{
    let started = Instant::now();
    loop {
        if !is_alive() {
            return Err(PortFileError::Timeout {
                path: port_file.to_path_buf(),
                timeout: started.elapsed(),
            });
        }
        if port_file.exists() {
            // Chromium / CEF may be mid-write the first time we see
            // the file; tolerate one IO error and retry.
            if let Ok(raw) = std::fs::read_to_string(port_file) {
                if !raw.is_empty() {
                    return parse_port_file(&raw, port_file);
                }
            }
        }
        if started.elapsed() >= MAX_WAIT {
            return Err(PortFileError::Timeout {
                path: port_file.to_path_buf(),
                timeout: MAX_WAIT,
            });
        }
        sleep(POLL_INTERVAL).await;
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::path::PathBuf;
    use std::sync::atomic::{AtomicBool, Ordering};
    use std::sync::Arc;

    fn temp_path(label: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "thalyn-cef-port-test-{}-{}",
            label,
            std::process::id()
        ));
        if dir.exists() {
            fs::remove_dir_all(&dir).unwrap();
        }
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn parses_canonical_two_line_payload() {
        let raw = "56123\n/devtools/browser/abcd-1234\n";
        let endpoint = parse_port_file(raw, Path::new("DevToolsActivePort")).unwrap();
        assert_eq!(endpoint.port, 56123);
        assert_eq!(endpoint.ws_path, "/devtools/browser/abcd-1234");
        assert_eq!(
            endpoint.ws_url(),
            "ws://127.0.0.1:56123/devtools/browser/abcd-1234"
        );
    }

    #[test]
    fn tolerates_no_trailing_newline() {
        let raw = "8080\n/devtools/browser/x";
        let endpoint = parse_port_file(raw, Path::new("DevToolsActivePort")).unwrap();
        assert_eq!(endpoint.port, 8080);
        assert_eq!(endpoint.ws_path, "/devtools/browser/x");
    }

    #[test]
    fn empty_payload_is_malformed() {
        let err = parse_port_file("", Path::new("DevToolsActivePort")).unwrap_err();
        assert!(matches!(err, PortFileError::Malformed { .. }));
    }

    #[test]
    fn missing_ws_path_is_malformed() {
        let err = parse_port_file("8080\n", Path::new("DevToolsActivePort")).unwrap_err();
        assert!(matches!(err, PortFileError::Malformed { .. }));
    }

    #[test]
    fn non_numeric_port_is_malformed() {
        let err = parse_port_file("nope\n/devtools/browser/x", Path::new("DevToolsActivePort"))
            .unwrap_err();
        assert!(matches!(err, PortFileError::Malformed { .. }));
    }

    #[test]
    fn ws_path_must_be_absolute() {
        let err = parse_port_file("8080\ndevtools/browser/x", Path::new("DevToolsActivePort"))
            .unwrap_err();
        assert!(matches!(err, PortFileError::Malformed { .. }));
    }

    #[tokio::test]
    async fn waits_for_file_then_parses_it() {
        let dir = temp_path("wait-ok");
        let port_file = dir.join("DevToolsActivePort");
        let port_file_for_writer = port_file.clone();

        let writer = tokio::spawn(async move {
            tokio::time::sleep(Duration::from_millis(80)).await;
            fs::write(&port_file_for_writer, "9001\n/devtools/browser/zzz").unwrap();
        });

        let endpoint = wait_for_port_file(&port_file, || true).await.unwrap();
        writer.await.unwrap();

        assert_eq!(endpoint.port, 9001);
        assert_eq!(endpoint.ws_path, "/devtools/browser/zzz");
    }

    #[tokio::test]
    async fn surfaces_early_death_via_is_alive() {
        let dir = temp_path("wait-dead");
        let port_file = dir.join("DevToolsActivePort");
        let alive = Arc::new(AtomicBool::new(true));
        let alive_for_killer = alive.clone();

        let killer = tokio::spawn(async move {
            tokio::time::sleep(Duration::from_millis(40)).await;
            alive_for_killer.store(false, Ordering::SeqCst);
        });

        let alive_check = alive.clone();
        let result =
            wait_for_port_file(&port_file, move || alive_check.load(Ordering::SeqCst)).await;
        killer.await.unwrap();

        assert!(matches!(result, Err(PortFileError::Timeout { .. })));
    }
}
