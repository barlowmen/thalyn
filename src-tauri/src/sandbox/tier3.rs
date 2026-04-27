//! Tier 3 — cloud sandbox for compute-heavy work.
//!
//! Tier 3 is a cloud sandbox the user opts into for heavy or GPU
//! work that they don't want their laptop running — long-form
//! benchmarking, model training kicked off from an agent, throwaway
//! environments for exploring a 10 GB dataset. Two providers ship
//! under one trait:
//!
//! * **E2B** — `e2b.dev` cloud sandboxes; HTTP REST API.
//! * **Daytona** — `daytona.io` cloud sandboxes; HTTP REST API.
//!
//! Both are credential-gated by the user — no Thalyn-side account,
//! no implicit cloud usage. The user pastes their own API key in
//! Settings → Observability (the v0.14.4 panel), and the Rust core
//! forwards it to the sandbox manager via the OS keychain via a
//! `secret.{provider}_api_key` slot.
//!
//! As with Tier 2, the v0.15 commit ships the trait surface,
//! detection, and typed error mapping; the actual HTTP integration
//! lands in a v0.15.x follow-up alongside the user-facing key
//! provisioning UI. Until then Tier3Sandbox::start surfaces a
//! typed `ApiIntegrationPending` error the escalation policy can
//! fall back from.

#![allow(dead_code)]

use std::path::{Path, PathBuf};

use async_trait::async_trait;

use super::{ExecOutput, Sandbox, SandboxError, SandboxSpec, SandboxTier};

/// Which cloud provider backs a Tier-3 sandbox.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Tier3Backend {
    E2b,
    Daytona,
}

impl Tier3Backend {
    pub fn name(self) -> &'static str {
        match self {
            Tier3Backend::E2b => "e2b",
            Tier3Backend::Daytona => "daytona",
        }
    }

    /// Env-var name the Rust core forwards from the keychain. Names
    /// are the same the user sees in the settings panel.
    pub fn api_key_env(self) -> &'static str {
        match self {
            Tier3Backend::E2b => "THALYN_E2B_API_KEY",
            Tier3Backend::Daytona => "THALYN_DAYTONA_API_KEY",
        }
    }

    /// Detect a configured backend via the env var. Preference order
    /// is E2B → Daytona; the user can override by clearing the one
    /// they don't want.
    pub fn detect() -> Option<Self> {
        [Tier3Backend::E2b, Tier3Backend::Daytona]
            .into_iter()
            .find(|candidate| {
                std::env::var(candidate.api_key_env())
                    .map(|v| !v.trim().is_empty())
                    .unwrap_or(false)
            })
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Tier3Unavailable {
    NoApiKeyConfigured,
    ApiIntegrationPending,
}

impl Tier3Unavailable {
    pub fn message(self) -> &'static str {
        match self {
            Tier3Unavailable::NoApiKeyConfigured => {
                "Tier 3 cloud sandbox needs an E2B or Daytona API key — \
                 paste one in Settings → Observability"
            }
            Tier3Unavailable::ApiIntegrationPending => {
                "Tier 3 cloud-API integration lands in a follow-up; \
                 escalate-to-Tier-1 is the v0.15 fallback (see docs/sandbox-tiers.md)"
            }
        }
    }
}

#[derive(Debug)]
pub struct Tier3Sandbox {
    run_id: String,
    workspace: PathBuf,
    backend: Tier3Backend,
}

impl Tier3Sandbox {
    pub async fn start(spec: SandboxSpec) -> Result<Self, SandboxError> {
        let backend = Tier3Backend::detect().ok_or_else(|| {
            SandboxError::Start(Tier3Unavailable::NoApiKeyConfigured.message().into())
        })?;
        Err(SandboxError::Start(format!(
            "{} (backend: {:?})",
            Tier3Unavailable::ApiIntegrationPending.message(),
            backend,
        )))
        .or_else(|err| {
            if false {
                Ok(Self {
                    run_id: spec.run_id.clone(),
                    workspace: spec.workspace.clone(),
                    backend,
                })
            } else {
                Err(err)
            }
        })
    }

    pub fn backend(&self) -> Tier3Backend {
        self.backend
    }
}

#[async_trait]
impl Sandbox for Tier3Sandbox {
    fn tier(&self) -> SandboxTier {
        SandboxTier::Tier3
    }

    fn run_id(&self) -> &str {
        &self.run_id
    }

    fn workspace(&self) -> &Path {
        &self.workspace
    }

    async fn exec(&self, _argv: &[String]) -> Result<ExecOutput, SandboxError> {
        Err(SandboxError::Exec(
            Tier3Unavailable::ApiIntegrationPending.message().into(),
        ))
    }

    async fn read_file(&self, _relative: &Path) -> Result<Vec<u8>, SandboxError> {
        Err(SandboxError::Exec(
            Tier3Unavailable::ApiIntegrationPending.message().into(),
        ))
    }

    async fn teardown(self: Box<Self>) -> Result<(), SandboxError> {
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;
    use std::sync::Mutex;

    static ENV_LOCK: Mutex<()> = Mutex::new(());

    fn temp_workspace(label: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "thalyn-tier3-test-{}-{}",
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
    #[allow(clippy::await_holding_lock)]
    async fn start_without_api_key_surfaces_no_api_key_error() {
        let _g = ENV_LOCK.lock().unwrap();
        std::env::remove_var(Tier3Backend::E2b.api_key_env());
        std::env::remove_var(Tier3Backend::Daytona.api_key_env());
        let workspace = temp_workspace("noapi");
        let err = Tier3Sandbox::start(SandboxSpec {
            run_id: "r_t3".into(),
            workspace: workspace.clone(),
            egress_allowlist: vec![],
        })
        .await
        .unwrap_err();
        let SandboxError::Start(msg) = err else {
            panic!("expected Start error");
        };
        assert!(msg.contains("API key"), "unexpected error: {msg}");
        let _ = std::fs::remove_dir_all(&workspace);
    }

    #[tokio::test]
    #[allow(clippy::await_holding_lock)]
    async fn start_with_api_key_surfaces_pending_integration_error() {
        let _g = ENV_LOCK.lock().unwrap();
        std::env::set_var(Tier3Backend::E2b.api_key_env(), "test-key");
        let workspace = temp_workspace("pending");
        let err = Tier3Sandbox::start(SandboxSpec {
            run_id: "r_t3".into(),
            workspace: workspace.clone(),
            egress_allowlist: vec![],
        })
        .await
        .unwrap_err();
        std::env::remove_var(Tier3Backend::E2b.api_key_env());
        let SandboxError::Start(msg) = err else {
            panic!("expected Start error");
        };
        assert!(msg.contains("integration lands"), "unexpected error: {msg}");
        let _ = std::fs::remove_dir_all(&workspace);
    }

    #[test]
    fn backend_env_vars_are_distinct() {
        assert_ne!(
            Tier3Backend::E2b.api_key_env(),
            Tier3Backend::Daytona.api_key_env()
        );
    }

    #[test]
    fn unavailable_messages_are_distinct() {
        assert_ne!(
            Tier3Unavailable::NoApiKeyConfigured.message(),
            Tier3Unavailable::ApiIntegrationPending.message()
        );
    }
}
