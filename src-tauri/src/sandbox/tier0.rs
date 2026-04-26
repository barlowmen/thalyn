//! Tier 0 — bare host process with a read-only workspace mount.
//!
//! Tier 0 is the minimum-overhead sandbox. The "container" is just
//! the host process; the workspace path is treated as read-only by
//! convention (writes via this surface are rejected; the agent could
//! still bypass via direct FS calls, which is *not* the threat we
//! defend against here — see ADR-0011 / `02-architecture.md` §9).
//! Used for tasks like "summarise this file" where overhead matters
//! more than confinement.

use std::path::{Path, PathBuf};

use async_trait::async_trait;
use tokio::process::Command;

use super::{join_within, ExecOutput, Sandbox, SandboxError, SandboxSpec, SandboxTier};

#[derive(Debug)]
pub struct Tier0Sandbox {
    run_id: String,
    workspace: PathBuf,
}

impl Tier0Sandbox {
    pub fn from_spec(spec: SandboxSpec) -> Result<Self, SandboxError> {
        if !spec.workspace.exists() {
            return Err(SandboxError::Start(format!(
                "workspace {} does not exist",
                spec.workspace.display()
            )));
        }
        Ok(Self {
            run_id: spec.run_id,
            workspace: spec.workspace,
        })
    }
}

#[async_trait]
impl Sandbox for Tier0Sandbox {
    fn tier(&self) -> SandboxTier {
        SandboxTier::Tier0
    }

    fn run_id(&self) -> &str {
        &self.run_id
    }

    fn workspace(&self) -> &Path {
        &self.workspace
    }

    async fn exec(&self, argv: &[String]) -> Result<ExecOutput, SandboxError> {
        let (program, args) = argv
            .split_first()
            .ok_or_else(|| SandboxError::Exec("argv is empty".into()))?;
        let output = Command::new(program)
            .args(args)
            .current_dir(&self.workspace)
            .output()
            .await?;
        Ok(ExecOutput {
            stdout: String::from_utf8_lossy(&output.stdout).into_owned(),
            stderr: String::from_utf8_lossy(&output.stderr).into_owned(),
            exit_code: output.status.code().unwrap_or(-1),
        })
    }

    async fn read_file(&self, relative: &Path) -> Result<Vec<u8>, SandboxError> {
        let resolved = join_within(&self.workspace, relative)?;
        let bytes = tokio::fs::read(&resolved).await?;
        Ok(bytes)
    }

    async fn teardown(self: Box<Self>) -> Result<(), SandboxError> {
        // Bare process; nothing to tear down.
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp_workspace(label: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "thalyn-sandbox-test-{}-{}",
            label,
            std::process::id()
        ));
        if dir.exists() {
            std::fs::remove_dir_all(&dir).unwrap();
        }
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    fn cleanup(path: &Path) {
        let _ = std::fs::remove_dir_all(path);
    }

    #[test]
    fn missing_workspace_is_rejected() {
        let err = Tier0Sandbox::from_spec(SandboxSpec {
            run_id: "r_x".into(),
            workspace: PathBuf::from("/path/that/does/not/exist"),
            egress_allowlist: vec![],
        })
        .unwrap_err();
        assert!(matches!(err, SandboxError::Start(_)));
    }

    #[tokio::test]
    async fn read_file_rejects_parent_dir_escape() {
        let workspace = temp_workspace("escape");
        let sandbox = Tier0Sandbox::from_spec(SandboxSpec {
            run_id: "r_escape".into(),
            workspace: workspace.clone(),
            egress_allowlist: vec![],
        })
        .unwrap();

        let err = sandbox
            .read_file(Path::new("../../etc/passwd"))
            .await
            .unwrap_err();
        assert!(matches!(err, SandboxError::PathEscaped(_)));

        cleanup(&workspace);
    }

    #[tokio::test]
    async fn read_file_returns_workspace_contents() {
        let workspace = temp_workspace("read");
        std::fs::write(workspace.join("hello.txt"), b"hello world").unwrap();

        let sandbox = Tier0Sandbox::from_spec(SandboxSpec {
            run_id: "r_read".into(),
            workspace: workspace.clone(),
            egress_allowlist: vec![],
        })
        .unwrap();

        let bytes = sandbox.read_file(Path::new("hello.txt")).await.unwrap();
        assert_eq!(bytes, b"hello world");

        cleanup(&workspace);
    }

    #[tokio::test]
    async fn exec_runs_in_workspace() {
        let workspace = temp_workspace("exec");
        std::fs::write(workspace.join("a.txt"), b"a").unwrap();
        std::fs::write(workspace.join("b.txt"), b"b").unwrap();

        let sandbox = Tier0Sandbox::from_spec(SandboxSpec {
            run_id: "r_exec".into(),
            workspace: workspace.clone(),
            egress_allowlist: vec![],
        })
        .unwrap();

        let output = sandbox.exec(&["ls".into()]).await.unwrap();
        assert_eq!(output.exit_code, 0);
        assert!(output.stdout.contains("a.txt"));
        assert!(output.stdout.contains("b.txt"));

        cleanup(&workspace);
    }

    #[tokio::test]
    async fn teardown_is_a_noop_for_tier_0() {
        let workspace = temp_workspace("teardown");
        let sandbox = Tier0Sandbox::from_spec(SandboxSpec {
            run_id: "r_teardown".into(),
            workspace: workspace.clone(),
            egress_allowlist: vec![],
        })
        .unwrap();
        Box::new(sandbox).teardown().await.unwrap();
        // Workspace untouched after teardown.
        assert!(workspace.exists());
        cleanup(&workspace);
    }
}
