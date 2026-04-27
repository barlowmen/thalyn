//! Sandbox abstraction for sub-agent isolation.
//!
//! Sub-agents that touch the filesystem or shell run inside a sandbox
//! whose tier is chosen per task. Tier 0 is a bare host process —
//! read-only access to a workspace, no isolation overhead, suitable for
//! "summarise this file" agents. Tier 1 lands a devcontainer + git
//! worktree with default-deny egress; Tier 2 (microVM) and Tier 3
//! (cloud) ship later.
//!
//! The trait is the surface every tier implements; a `SandboxManager`
//! owns the lifecycle for in-flight sandboxes so the runner doesn't
//! need to reach into individual implementations.

#![allow(dead_code)]

mod manager;
mod tier0;
mod tier1;
mod tier2;
mod tier3;

pub use manager::SandboxManager;

use std::path::{Path, PathBuf};

use async_trait::async_trait;
use thiserror::Error;

/// Tier classification — surfaced to the renderer for the badge on
/// each sub-agent tile and the inspector pane.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SandboxTier {
    /// Bare process, read-only workspace mount. No isolation overhead.
    Tier0,
    /// Devcontainer + per-agent git worktree, default-deny egress.
    Tier1,
    /// MicroVM — Firecracker / Lima / Apple Containerization.
    /// Deferred (`02-architecture.md` §8, ADR-0011).
    Tier2,
    /// Cloud sandbox — E2B / Daytona. Opt-in only.
    /// Deferred (ADR-0011).
    Tier3,
}

impl SandboxTier {
    /// Stable wire name (`"tier_0"` … `"tier_3"`) for the JSON-RPC
    /// surface and the renderer's tile badge.
    pub fn wire_name(self) -> &'static str {
        match self {
            SandboxTier::Tier0 => "tier_0",
            SandboxTier::Tier1 => "tier_1",
            SandboxTier::Tier2 => "tier_2",
            SandboxTier::Tier3 => "tier_3",
        }
    }
}

#[derive(Debug, Error)]
pub enum SandboxError {
    #[error("sandbox start failed: {0}")]
    Start(String),
    #[error("sandbox exec failed: {0}")]
    Exec(String),
    #[error("sandbox teardown failed: {0}")]
    Teardown(String),
    #[error("path {} escapes the sandbox workspace", .0.display())]
    PathEscaped(PathBuf),
    #[error("write rejected on a read-only sandbox")]
    ReadOnly,
    #[error("io: {0}")]
    Io(#[from] std::io::Error),
}

/// Specification for one sandbox lifecycle. The runner builds a fresh
/// spec per sub-agent dispatch and hands it to the manager.
#[derive(Debug, Clone)]
pub struct SandboxSpec {
    /// Run id of the sub-agent the sandbox belongs to.
    pub run_id: String,
    /// Root the workspace mounts read-only inside the sandbox.
    pub workspace: PathBuf,
    /// Hostnames the sandbox is allowed to reach. Empty list keeps the
    /// default-deny posture (Tier 1+); ignored on Tier 0 since the
    /// host network is already in scope.
    pub egress_allowlist: Vec<String>,
}

/// Output of one in-sandbox command execution.
#[derive(Debug, Clone)]
pub struct ExecOutput {
    pub stdout: String,
    pub stderr: String,
    pub exit_code: i32,
}

#[async_trait]
pub trait Sandbox: Send + Sync {
    fn tier(&self) -> SandboxTier;
    fn run_id(&self) -> &str;
    fn workspace(&self) -> &Path;

    /// Run a command inside the sandbox. The command is parsed as a
    /// program plus arguments; tiers that pass through to a shell
    /// (e.g. Tier 1's `docker exec`) handle that internally.
    async fn exec(&self, argv: &[String]) -> Result<ExecOutput, SandboxError>;

    /// Read a file inside the workspace. The path must be relative;
    /// any attempt to escape via `..` is rejected.
    async fn read_file(&self, relative: &Path) -> Result<Vec<u8>, SandboxError>;

    /// Tear down — close any container, clean up worktree state.
    /// Called on agent completion or when the user kills the run.
    async fn teardown(self: Box<Self>) -> Result<(), SandboxError>;
}

/// Reject relative paths whose normalised form escapes ``base``.
/// Reusable across tier implementations so each one enforces the
/// no-escape invariant identically.
pub(crate) fn join_within(base: &Path, relative: &Path) -> Result<PathBuf, SandboxError> {
    if relative.is_absolute() {
        return Err(SandboxError::PathEscaped(relative.to_path_buf()));
    }
    let mut out = base.to_path_buf();
    for component in relative.components() {
        match component {
            std::path::Component::Normal(name) => out.push(name),
            std::path::Component::CurDir => {}
            std::path::Component::ParentDir
            | std::path::Component::RootDir
            | std::path::Component::Prefix(_) => {
                return Err(SandboxError::PathEscaped(relative.to_path_buf()));
            }
        }
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn join_within_accepts_relative_paths() {
        let base = Path::new("/work");
        let resolved = join_within(base, Path::new("src/main.rs")).unwrap();
        assert_eq!(resolved, Path::new("/work/src/main.rs"));
    }

    #[test]
    fn join_within_rejects_parent_dir_escape() {
        let base = Path::new("/work");
        let err = join_within(base, Path::new("../etc/passwd")).unwrap_err();
        assert!(matches!(err, SandboxError::PathEscaped(_)));
    }

    #[test]
    fn join_within_rejects_absolute_paths() {
        let base = Path::new("/work");
        let err = join_within(base, Path::new("/etc/passwd")).unwrap_err();
        assert!(matches!(err, SandboxError::PathEscaped(_)));
    }

    #[test]
    fn tier_wire_names_are_stable() {
        assert_eq!(SandboxTier::Tier0.wire_name(), "tier_0");
        assert_eq!(SandboxTier::Tier1.wire_name(), "tier_1");
        assert_eq!(SandboxTier::Tier2.wire_name(), "tier_2");
        assert_eq!(SandboxTier::Tier3.wire_name(), "tier_3");
    }
}
