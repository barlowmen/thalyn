//! Auth-backend trait — splits *how to authenticate* from *which model
//! to call*.
//!
//! Mirrors the Python ``AuthBackend`` Protocol in
//! ``brain/thalyn_brain/provider/auth.py``. The Rust side carries the
//! type surface the desktop core needs for capability probing,
//! provider-list rendering, and first-run setup; the Python side is
//! where the auth decision lands inside the SDK call. The two are kept
//! in lockstep through the shared snake_case serde rendering of
//! ``AuthBackendKind``.
//!
//! Several methods are exposed as the public surface that subsequent
//! commits in this phase wire into Tauri commands and the IPC
//! dispatcher; suppress the dead-code lint here so the trait surface
//! can be authored coherently before being fully consumed.

#![allow(dead_code)]

use async_trait::async_trait;
use serde::{Deserialize, Serialize};
use thiserror::Error;

/// Identifier for the credential source. Wire / storage form matches
/// the Python ``AuthBackendKind`` and the ``auth_backends.kind`` SQLite
/// column (migration 003).
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq, Hash)]
#[serde(rename_all = "snake_case")]
pub enum AuthBackendKind {
    ClaudeSubscription,
    AnthropicApi,
    OpenAiCompat,
    Ollama,
    LlamaCpp,
    Mlx,
}

impl AuthBackendKind {
    /// Stable string id used by JSON-RPC payloads and the SQLite store.
    pub fn as_str(&self) -> &'static str {
        match self {
            AuthBackendKind::ClaudeSubscription => "claude_subscription",
            AuthBackendKind::AnthropicApi => "anthropic_api",
            AuthBackendKind::OpenAiCompat => "openai_compat",
            AuthBackendKind::Ollama => "ollama",
            AuthBackendKind::LlamaCpp => "llama_cpp",
            AuthBackendKind::Mlx => "mlx",
        }
    }
}

/// Result of asking an auth backend "are you ready right now?".
///
/// The four states ``(detected, authenticated)`` carries are:
///   * ``(true, true)``  — happy path; the backend is ready.
///   * ``(true, false)`` — backend is installed but no credential. The
///     UI offers a login / paste step.
///   * ``(false, false)`` — substrate not reachable (CLI not on PATH,
///     endpoint not responding). The UI offers install help.
///   * ``error`` populated — probe itself failed; treat as
///     ``(false, false)`` and surface the message.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct AuthProbeResult {
    pub detected: bool,
    pub authenticated: bool,
    pub detail: Option<String>,
    pub error: Option<String>,
}

impl AuthProbeResult {
    pub fn ready(detail: impl Into<String>) -> Self {
        Self {
            detected: true,
            authenticated: true,
            detail: Some(detail.into()),
            error: None,
        }
    }

    pub fn detected_unauthenticated(detail: impl Into<String>) -> Self {
        Self {
            detected: true,
            authenticated: false,
            detail: Some(detail.into()),
            error: None,
        }
    }

    pub fn not_detected(detail: impl Into<String>) -> Self {
        Self {
            detected: false,
            authenticated: false,
            detail: Some(detail.into()),
            error: None,
        }
    }

    pub fn errored(error: impl Into<String>) -> Self {
        Self {
            detected: false,
            authenticated: false,
            detail: None,
            error: Some(error.into()),
        }
    }
}

#[derive(Debug, Error)]
pub enum AuthBackendError {
    #[error("auth backend {kind:?} not detected: {detail}")]
    NotDetected {
        kind: AuthBackendKind,
        detail: String,
    },
    #[error("auth backend {kind:?} not authenticated: {detail}")]
    NotAuthenticated {
        kind: AuthBackendKind,
        detail: String,
    },
    #[error("auth backend {kind:?} probe failed: {detail}")]
    Probe {
        kind: AuthBackendKind,
        detail: String,
    },
}

/// Runtime trait for an auth backend.
///
/// Decoupled from ``LlmProvider`` so a single provider can be composed
/// with either subscription or API-key auth (the v0.22 split per
/// ``02-architecture.md`` §7).
#[async_trait]
pub trait AuthBackend: Send + Sync {
    /// Identifier for the credential source.
    fn kind(&self) -> AuthBackendKind;

    /// Cheap reachability + auth check.
    async fn probe(&self) -> AuthProbeResult;

    /// Raise if the backend can't be used right now. Adapters that
    /// drive a one-shot login flow do it from here.
    async fn ensure_ready(&self) -> Result<(), AuthBackendError>;
}

#[cfg(test)]
mod tests {
    use super::*;

    struct AlwaysReady;

    #[async_trait]
    impl AuthBackend for AlwaysReady {
        fn kind(&self) -> AuthBackendKind {
            AuthBackendKind::ClaudeSubscription
        }
        async fn probe(&self) -> AuthProbeResult {
            AuthProbeResult::ready("test backend")
        }
        async fn ensure_ready(&self) -> Result<(), AuthBackendError> {
            Ok(())
        }
    }

    #[tokio::test]
    async fn dyn_object_safe() {
        let backend: Box<dyn AuthBackend> = Box::new(AlwaysReady);
        assert!(matches!(
            backend.kind(),
            AuthBackendKind::ClaudeSubscription
        ));
        let probe = backend.probe().await;
        assert!(probe.authenticated);
        assert!(backend.ensure_ready().await.is_ok());
    }

    #[test]
    fn kind_as_str_matches_python_enum() {
        assert_eq!(
            AuthBackendKind::ClaudeSubscription.as_str(),
            "claude_subscription"
        );
        assert_eq!(AuthBackendKind::AnthropicApi.as_str(), "anthropic_api");
        assert_eq!(AuthBackendKind::OpenAiCompat.as_str(), "openai_compat");
        assert_eq!(AuthBackendKind::Ollama.as_str(), "ollama");
        assert_eq!(AuthBackendKind::LlamaCpp.as_str(), "llama_cpp");
        assert_eq!(AuthBackendKind::Mlx.as_str(), "mlx");
    }

    #[test]
    fn probe_result_serde_roundtrip() {
        let result = AuthProbeResult::ready("Claude subscription (oauth_token)");
        let json = serde_json::to_string(&result).expect("serialize probe");
        // Wire shape is camelCase, matching the Python `to_wire`.
        assert!(json.contains("\"detected\":true"));
        assert!(json.contains("\"authenticated\":true"));
        let round: AuthProbeResult = serde_json::from_str(&json).expect("deserialize probe");
        assert_eq!(round, result);
    }

    #[test]
    fn errored_probe_carries_message() {
        let result = AuthProbeResult::errored("subprocess died");
        assert!(!result.detected);
        assert!(!result.authenticated);
        assert_eq!(result.error.as_deref(), Some("subprocess died"));
    }
}
