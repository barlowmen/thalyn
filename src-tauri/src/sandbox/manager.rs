//! Sandbox lifecycle manager.
//!
//! Owns the in-flight sandboxes keyed by run id so the Rust core can
//! dispatch and tear them down without each caller having to track
//! handles. The manager is intentionally narrow — start, exec, read,
//! teardown — and delegates the actual confinement to the tier
//! implementation behind the `Sandbox` trait.

use std::collections::HashMap;
use std::path::Path;
use std::sync::Arc;

use tokio::sync::RwLock;

use super::{tier0::Tier0Sandbox, ExecOutput, Sandbox, SandboxError, SandboxSpec, SandboxTier};

#[derive(Default)]
pub struct SandboxManager {
    inner: Arc<RwLock<HashMap<String, Box<dyn Sandbox>>>>,
}

impl SandboxManager {
    pub fn new() -> Self {
        Self::default()
    }

    /// Allocate a sandbox at the requested tier and return its run id
    /// for subsequent ``exec`` / ``read_file`` / ``teardown`` calls.
    pub async fn start(
        &self,
        tier: SandboxTier,
        spec: SandboxSpec,
    ) -> Result<String, SandboxError> {
        let run_id = spec.run_id.clone();
        let sandbox: Box<dyn Sandbox> = match tier {
            SandboxTier::Tier0 => Box::new(Tier0Sandbox::from_spec(spec)?),
            SandboxTier::Tier1 | SandboxTier::Tier2 | SandboxTier::Tier3 => {
                return Err(SandboxError::Start(format!(
                    "{} not implemented",
                    tier.wire_name()
                )));
            }
        };
        let mut inner = self.inner.write().await;
        if inner.contains_key(&run_id) {
            return Err(SandboxError::Start(format!(
                "sandbox already running for run {run_id}"
            )));
        }
        inner.insert(run_id.clone(), sandbox);
        Ok(run_id)
    }

    pub async fn tier_of(&self, run_id: &str) -> Option<SandboxTier> {
        self.inner.read().await.get(run_id).map(|s| s.tier())
    }

    pub async fn exec(&self, run_id: &str, argv: &[String]) -> Result<ExecOutput, SandboxError> {
        let inner = self.inner.read().await;
        let sandbox = inner
            .get(run_id)
            .ok_or_else(|| SandboxError::Exec(format!("unknown run {run_id}")))?;
        sandbox.exec(argv).await
    }

    pub async fn read_file(&self, run_id: &str, relative: &Path) -> Result<Vec<u8>, SandboxError> {
        let inner = self.inner.read().await;
        let sandbox = inner
            .get(run_id)
            .ok_or_else(|| SandboxError::Exec(format!("unknown run {run_id}")))?;
        sandbox.read_file(relative).await
    }

    pub async fn teardown(&self, run_id: &str) -> Result<(), SandboxError> {
        let mut inner = self.inner.write().await;
        let sandbox = inner
            .remove(run_id)
            .ok_or_else(|| SandboxError::Teardown(format!("unknown run {run_id}")))?;
        sandbox.teardown().await
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    fn temp_workspace(label: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "thalyn-sandbox-mgr-test-{}-{}",
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
    async fn start_then_teardown_round_trip() {
        let workspace = temp_workspace("round_trip");
        let manager = SandboxManager::new();
        let run_id = manager
            .start(
                SandboxTier::Tier0,
                SandboxSpec {
                    run_id: "r_a".into(),
                    workspace: workspace.clone(),
                    egress_allowlist: vec![],
                },
            )
            .await
            .unwrap();
        assert_eq!(run_id, "r_a");
        assert_eq!(manager.tier_of("r_a").await, Some(SandboxTier::Tier0));
        manager.teardown("r_a").await.unwrap();
        assert!(manager.tier_of("r_a").await.is_none());
        let _ = std::fs::remove_dir_all(&workspace);
    }

    #[tokio::test]
    async fn duplicate_start_fails() {
        let workspace = temp_workspace("duplicate");
        let manager = SandboxManager::new();
        manager
            .start(
                SandboxTier::Tier0,
                SandboxSpec {
                    run_id: "r_b".into(),
                    workspace: workspace.clone(),
                    egress_allowlist: vec![],
                },
            )
            .await
            .unwrap();
        let err = manager
            .start(
                SandboxTier::Tier0,
                SandboxSpec {
                    run_id: "r_b".into(),
                    workspace: workspace.clone(),
                    egress_allowlist: vec![],
                },
            )
            .await
            .unwrap_err();
        assert!(matches!(err, SandboxError::Start(_)));
        manager.teardown("r_b").await.unwrap();
        let _ = std::fs::remove_dir_all(&workspace);
    }

    #[tokio::test]
    async fn higher_tiers_not_yet_implemented() {
        let workspace = temp_workspace("higher");
        let manager = SandboxManager::new();
        let err = manager
            .start(
                SandboxTier::Tier1,
                SandboxSpec {
                    run_id: "r_c".into(),
                    workspace: workspace.clone(),
                    egress_allowlist: vec![],
                },
            )
            .await
            .unwrap_err();
        assert!(matches!(err, SandboxError::Start(_)));
        let _ = std::fs::remove_dir_all(&workspace);
    }
}
