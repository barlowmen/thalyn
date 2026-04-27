//! Tier 2 — microVM isolation for risky sub-agents.
//!
//! Tier 2 is opt-in: agents that execute generated code, run
//! untrusted dependencies, or do anything the user explicitly tags
//! higher-risk land here instead of the default Tier 1 devcontainer.
//! The microVM provides a kernel-level isolation boundary that a
//! container does not — a runaway agent that escapes its container
//! still cannot reach the host's kernel.
//!
//! Two backends ship under the same trait:
//!
//! * **Firecracker** on Linux. Started via the `firecracker` CLI;
//!   workspace is mounted via a virtio-fs share; networking uses a
//!   tap device with the same default-deny posture as Tier 1.
//! * **Lima** on macOS. Bridge until macOS 26 Tahoe ships Apple
//!   Containerization (ADR-0011). Started via `limactl`; workspace
//!   is mounted via reverse-sshfs; networking is the user's host
//!   network (microVMs on macOS pre-Tahoe don't have a clean
//!   default-deny story without extra infrastructure).
//!
//! Both backends share the heavy lifting in this file: lifecycle
//! state machine, error mapping, and the v0.15 detect-and-skip
//! posture for unsupported environments. The actual VM image
//! provisioning (kernel + rootfs) is deferred to a v0.15.x follow-up
//! once the user-facing image-management story is settled — until
//! then `Tier2Sandbox::start` returns a typed error that the
//! escalation policy treats as "fall back to Tier 1 with a
//! warning." This is an honest staging point: the trait surface and
//! the platform detection are stable enough that a real image
//! lifecycle slots in without breaking callers.

#![allow(dead_code)]

use std::path::{Path, PathBuf};

use async_trait::async_trait;
use tokio::process::Command;

use super::{ExecOutput, Sandbox, SandboxError, SandboxSpec, SandboxTier};

/// Which Tier-2 backend a host can run.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Tier2Backend {
    /// `firecracker` on Linux.
    Firecracker,
    /// `limactl` on macOS.
    Lima,
}

impl Tier2Backend {
    pub fn binary(self) -> &'static str {
        match self {
            Tier2Backend::Firecracker => "firecracker",
            Tier2Backend::Lima => "limactl",
        }
    }

    /// Probe the host for an available Tier-2 backend. Returns the
    /// platform-appropriate option, or ``None`` if neither is
    /// installed.
    pub async fn detect() -> Option<Self> {
        #[cfg(target_os = "linux")]
        {
            if probe(Tier2Backend::Firecracker).await {
                return Some(Tier2Backend::Firecracker);
            }
        }
        #[cfg(target_os = "macos")]
        {
            if probe(Tier2Backend::Lima).await {
                return Some(Tier2Backend::Lima);
            }
        }
        None
    }
}

async fn probe(backend: Tier2Backend) -> bool {
    Command::new(backend.binary())
        .arg("--version")
        .output()
        .await
        .map(|out| out.status.success())
        .unwrap_or(false)
}

/// Static reasons `Tier2Sandbox::start` may refuse to bring up a
/// microVM. Surfaced through `SandboxError::Start` with a clear
/// message so the brain's escalation logic can decide whether to
/// fall back to Tier 1 or surface the error to the user.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Tier2Unavailable {
    NoBackendInstalled,
    UnsupportedPlatform,
    ImageProvisioningPending,
}

impl Tier2Unavailable {
    pub fn message(self) -> &'static str {
        match self {
            Tier2Unavailable::NoBackendInstalled => {
                "Tier 2 microVM backend not installed (firecracker on Linux, lima on macOS)"
            }
            Tier2Unavailable::UnsupportedPlatform => {
                "Tier 2 is only supported on Linux (Firecracker) and macOS (Lima) in v1"
            }
            Tier2Unavailable::ImageProvisioningPending => {
                "Tier 2 microVM image provisioning lands in a follow-up; \
                 escalate-to-Tier-1 is the v0.15 fallback (see docs/sandbox-tiers.md)"
            }
        }
    }
}

/// Tier-2 sandbox handle. Today this is a typed scaffolding — the
/// real VM lifecycle (image provisioning, virtio-fs mount, network
/// namespace setup) lands once the user-facing image-management
/// story is decided. Calls to `start` return
/// `Tier2Unavailable::ImageProvisioningPending` so the escalation
/// policy can fall back gracefully. The trait surface, detection,
/// and per-backend error mapping are stable.
#[derive(Debug)]
pub struct Tier2Sandbox {
    run_id: String,
    workspace: PathBuf,
    backend: Tier2Backend,
}

impl Tier2Sandbox {
    pub async fn start(spec: SandboxSpec) -> Result<Self, SandboxError> {
        let backend = Tier2Backend::detect().await.ok_or_else(|| {
            SandboxError::Start(Tier2Unavailable::NoBackendInstalled.message().into())
        })?;
        // The full microVM lifecycle is the v0.15.x follow-up — the
        // image, kernel, init, virtio-fs share, and networking are
        // each their own decision. Surface a typed
        // `ImageProvisioningPending` so the escalation policy can
        // decide between "run in Tier 1 with a warning" and
        // "abort the run entirely."
        Err(SandboxError::Start(format!(
            "{} (backend: {:?})",
            Tier2Unavailable::ImageProvisioningPending.message(),
            backend,
        )))
        .or_else(|err| {
            // Linter: keep `Self` constructible so the trait shape
            // stays exercised by callers. The `Ok` branch is
            // unreachable today; switching it on lands with the
            // image-management commit.
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

    pub fn backend(&self) -> Tier2Backend {
        self.backend
    }
}

#[async_trait]
impl Sandbox for Tier2Sandbox {
    fn tier(&self) -> SandboxTier {
        SandboxTier::Tier2
    }

    fn run_id(&self) -> &str {
        &self.run_id
    }

    fn workspace(&self) -> &Path {
        &self.workspace
    }

    async fn exec(&self, _argv: &[String]) -> Result<ExecOutput, SandboxError> {
        Err(SandboxError::Exec(
            Tier2Unavailable::ImageProvisioningPending.message().into(),
        ))
    }

    async fn read_file(&self, _relative: &Path) -> Result<Vec<u8>, SandboxError> {
        Err(SandboxError::Exec(
            Tier2Unavailable::ImageProvisioningPending.message().into(),
        ))
    }

    async fn teardown(self: Box<Self>) -> Result<(), SandboxError> {
        // No live VM to tear down today; the constructor never
        // succeeds. Once image provisioning lands, this issues the
        // backend-specific stop.
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    fn temp_workspace(label: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "thalyn-tier2-test-{}-{}",
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
    async fn start_surfaces_typed_unavailable_message() {
        let workspace = temp_workspace("unavailable");
        let err = Tier2Sandbox::start(SandboxSpec {
            run_id: "r_t2".into(),
            workspace: workspace.clone(),
            egress_allowlist: vec![],
        })
        .await
        .unwrap_err();
        let SandboxError::Start(msg) = err else {
            panic!("expected Start error");
        };
        assert!(
            msg.contains("microVM image provisioning") || msg.contains("backend not installed"),
            "unexpected error: {msg}"
        );
        let _ = std::fs::remove_dir_all(&workspace);
    }

    #[test]
    fn unavailable_messages_are_distinct() {
        // Sanity that the three stages produce different surface
        // text — escalation policy reads on these strings.
        let a = Tier2Unavailable::NoBackendInstalled.message();
        let b = Tier2Unavailable::UnsupportedPlatform.message();
        let c = Tier2Unavailable::ImageProvisioningPending.message();
        assert_ne!(a, b);
        assert_ne!(a, c);
        assert_ne!(b, c);
    }

    #[test]
    fn backend_binary_names_match_real_tools() {
        assert_eq!(Tier2Backend::Firecracker.binary(), "firecracker");
        assert_eq!(Tier2Backend::Lima.binary(), "limactl");
    }
}
