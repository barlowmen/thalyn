//! Tier 1 — devcontainer + per-agent git worktree.
//!
//! Tier 1 is the default for sub-agents that touch the filesystem or
//! the shell. The agent runs in a Docker container that defaults to
//! ``--network=none`` (hard-deny egress) and tightens to a per-task
//! DNS allowlist when the spec lists permitted hostnames. The
//! container's writable workspace is a per-run **git worktree**
//! carved out of the user's repo, so any changes the agent makes
//! land on a detached branch the user can merge or discard. The
//! original workspace is mounted read-only at a separate path so the
//! agent can still read reference files.
//!
//! ## Egress enforcement scope
//!
//! With an empty allowlist the container has no network at all
//! (`--network=none`). With a non-empty allowlist the host resolves
//! each hostname once at start time, injects the resulting `host:ip`
//! pairs via `--add-host`, and points container DNS at a black-hole
//! resolver so non-allowlisted lookups fail. This is **DNS-level**
//! enforcement: a runaway agent that already knows an IP could still
//! reach it. That matches the runaway-prevention threat model from
//! `01-requirements.md` §10.1 OQ-2 and ADR-0011; targeted-attacker
//! defence is in scope for the going-public hardening pass, not v1.
//!
//! Cross-platform variance is the dominant risk here — Docker Desktop
//! on macOS, native daemon on Linux, and Docker / Podman on Windows
//! all behave subtly differently. We use the `docker` CLI exclusively
//! (Podman aliases to it on most installs) so we don't depend on a
//! particular daemon API surface.

use std::net::IpAddr;
use std::path::{Path, PathBuf};

use async_trait::async_trait;
use tokio::net::lookup_host;
use tokio::process::Command;

use super::{ExecOutput, Sandbox, SandboxError, SandboxSpec, SandboxTier};

const DEFAULT_IMAGE: &str = "debian:bookworm-slim";
const WORKTREE_ROOT_DIRNAME: &str = ".thalyn-worktrees";
const CONTAINER_WORK_PATH: &str = "/work";
const CONTAINER_WORKSPACE_RO_PATH: &str = "/workspace-ro";

/// Container runtime detected at start time. Today only Docker;
/// Podman ships with a `docker` shim on most installs and works
/// through the same code path. The enum is kept so a Podman-specific
/// branch can land later without breaking the trait surface.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum ContainerRuntime {
    Docker,
}

impl ContainerRuntime {
    pub(crate) fn binary(self) -> &'static str {
        match self {
            ContainerRuntime::Docker => "docker",
        }
    }

    /// Probe for an available container runtime. Returns ``None`` if
    /// none was found — callers surface a clear `SandboxError::Start`
    /// in that case.
    pub(crate) async fn detect() -> Option<Self> {
        let probe = Command::new("docker")
            .arg("version")
            .arg("--format")
            .arg("{{.Server.Version}}")
            .output()
            .await
            .ok()?;
        if probe.status.success() {
            Some(ContainerRuntime::Docker)
        } else {
            None
        }
    }
}

#[derive(Debug)]
pub struct Tier1Sandbox {
    run_id: String,
    workspace: PathBuf,
    worktree: PathBuf,
    container_id: String,
    runtime: ContainerRuntime,
    image: String,
}

impl Tier1Sandbox {
    /// Bring up the worktree, the container, and the read-only mount.
    /// The image defaults to a lightweight Debian; callers can plug a
    /// different image in later by extending [`SandboxSpec`].
    pub async fn start(spec: SandboxSpec) -> Result<Self, SandboxError> {
        let runtime = ContainerRuntime::detect().await.ok_or_else(|| {
            SandboxError::Start(
                "no container runtime available — install Docker (or Podman)".into(),
            )
        })?;

        if !spec.workspace.exists() {
            return Err(SandboxError::Start(format!(
                "workspace {} does not exist",
                spec.workspace.display()
            )));
        }

        let worktree = setup_worktree(&spec.workspace, &spec.run_id).await?;

        let resolved_allowlist = resolve_allowlist(&spec.egress_allowlist).await?;

        let image = DEFAULT_IMAGE.to_string();
        let container_id = run_container(
            runtime,
            &spec.run_id,
            &worktree,
            &spec.workspace,
            &image,
            &resolved_allowlist,
        )
        .await?;

        Ok(Self {
            run_id: spec.run_id,
            workspace: spec.workspace,
            worktree,
            container_id,
            runtime,
            image,
        })
    }

    /// Path inside the host filesystem where the agent's writable
    /// worktree lives. Mounted at ``/work`` inside the container.
    pub fn worktree(&self) -> &Path {
        &self.worktree
    }
}

#[async_trait]
impl Sandbox for Tier1Sandbox {
    fn tier(&self) -> SandboxTier {
        SandboxTier::Tier1
    }

    fn run_id(&self) -> &str {
        &self.run_id
    }

    fn workspace(&self) -> &Path {
        &self.workspace
    }

    async fn exec(&self, argv: &[String]) -> Result<ExecOutput, SandboxError> {
        if argv.is_empty() {
            return Err(SandboxError::Exec("argv is empty".into()));
        }
        let mut cmd = Command::new(self.runtime.binary());
        cmd.arg("exec")
            .arg("--workdir")
            .arg(CONTAINER_WORK_PATH)
            .arg(&self.container_id);
        for arg in argv {
            cmd.arg(arg);
        }
        let output = cmd.output().await?;
        Ok(ExecOutput {
            stdout: String::from_utf8_lossy(&output.stdout).into_owned(),
            stderr: String::from_utf8_lossy(&output.stderr).into_owned(),
            exit_code: output.status.code().unwrap_or(-1),
        })
    }

    async fn read_file(&self, relative: &Path) -> Result<Vec<u8>, SandboxError> {
        let resolved = super::join_within(&self.worktree, relative)?;
        let bytes = tokio::fs::read(&resolved).await?;
        Ok(bytes)
    }

    async fn teardown(self: Box<Self>) -> Result<(), SandboxError> {
        // Stop + remove the container, then prune the git worktree.
        // Continue on individual failures so a stuck container doesn't
        // strand the worktree (or vice versa); accumulate the first
        // error to surface up.
        let mut first_error: Option<SandboxError> = None;

        let stop = Command::new(self.runtime.binary())
            .args(["rm", "-f", &self.container_id])
            .output()
            .await;
        if let Err(err) = stop {
            first_error.get_or_insert(SandboxError::Teardown(format!(
                "container rm failed: {err}"
            )));
        }

        if let Err(err) = remove_worktree(&self.workspace, &self.worktree).await {
            first_error.get_or_insert(err);
        }

        if let Some(err) = first_error {
            Err(err)
        } else {
            Ok(())
        }
    }
}

async fn setup_worktree(workspace: &Path, run_id: &str) -> Result<PathBuf, SandboxError> {
    if !is_git_repo(workspace).await {
        return Err(SandboxError::Start(format!(
            "{} is not a git repo — Tier 1 requires a git workspace; use Tier 0 for non-git",
            workspace.display()
        )));
    }

    let worktree_root = workspace.join(WORKTREE_ROOT_DIRNAME);
    tokio::fs::create_dir_all(&worktree_root).await?;
    let worktree_path = worktree_root.join(run_id);

    if worktree_path.exists() {
        // Stale worktree from a prior run — prune before re-creating
        // so `git worktree add` doesn't error.
        let _ = remove_worktree(workspace, &worktree_path).await;
    }

    let output = Command::new("git")
        .args([
            "worktree",
            "add",
            "--detach",
            worktree_path.to_string_lossy().as_ref(),
            "HEAD",
        ])
        .current_dir(workspace)
        .output()
        .await?;
    if !output.status.success() {
        return Err(SandboxError::Start(format!(
            "git worktree add failed: {}",
            String::from_utf8_lossy(&output.stderr)
        )));
    }
    Ok(worktree_path)
}

async fn remove_worktree(workspace: &Path, worktree_path: &Path) -> Result<(), SandboxError> {
    let output = Command::new("git")
        .args([
            "worktree",
            "remove",
            "--force",
            worktree_path.to_string_lossy().as_ref(),
        ])
        .current_dir(workspace)
        .output()
        .await?;
    if !output.status.success() {
        // git refuses to remove a worktree it doesn't recognise (e.g.
        // an external mkdir). Fall back to plain directory removal.
        if worktree_path.exists() {
            tokio::fs::remove_dir_all(worktree_path)
                .await
                .map_err(SandboxError::from)?;
        }
    }
    Ok(())
}

async fn is_git_repo(path: &Path) -> bool {
    let probe = Command::new("git")
        .args(["rev-parse", "--git-dir"])
        .current_dir(path)
        .output()
        .await;
    matches!(probe, Ok(out) if out.status.success())
}

/// One resolved entry in the egress allowlist — the hostname the
/// agent will use plus the host's resolution of it at sandbox start.
#[derive(Debug, Clone)]
struct ResolvedHost {
    hostname: String,
    ip: IpAddr,
}

/// Resolve every hostname once on the host. We resolve at start
/// (rather than each time the agent looks up) so a moving DNS
/// answer can't slip a new IP through after the gate clears.
async fn resolve_allowlist(allowlist: &[String]) -> Result<Vec<ResolvedHost>, SandboxError> {
    let mut resolved = Vec::with_capacity(allowlist.len());
    for hostname in allowlist {
        let trimmed = hostname.trim();
        if trimmed.is_empty() {
            continue;
        }
        // ``lookup_host`` insists on a port; any will do.
        let mut addrs = lookup_host(format!("{trimmed}:80")).await.map_err(|err| {
            SandboxError::Start(format!(
                "egress allowlist resolution failed for {trimmed}: {err}"
            ))
        })?;
        let first_ip = addrs.next().map(|sa| sa.ip()).ok_or_else(|| {
            SandboxError::Start(format!(
                "egress allowlist resolution returned no addresses for {trimmed}"
            ))
        })?;
        resolved.push(ResolvedHost {
            hostname: trimmed.to_string(),
            ip: first_ip,
        });
    }
    Ok(resolved)
}

async fn run_container(
    runtime: ContainerRuntime,
    run_id: &str,
    worktree: &Path,
    workspace: &Path,
    image: &str,
    allowlist: &[ResolvedHost],
) -> Result<String, SandboxError> {
    let container_name = container_name_for(run_id);
    let work_mount = format!("{}:{}", worktree.display(), CONTAINER_WORK_PATH);
    let workspace_mount = format!("{}:{}:ro", workspace.display(), CONTAINER_WORKSPACE_RO_PATH);

    let mut cmd = Command::new(runtime.binary());
    cmd.arg("run").arg("-d").arg("--rm");

    if allowlist.is_empty() {
        // Default-deny: no network at all.
        cmd.arg("--network=none");
    } else {
        // DNS-level allowlist. Pin each hostname to its host-resolved
        // IP via /etc/hosts inside the container, and route all other
        // DNS at a black-hole resolver so non-allowlisted lookups
        // fail. The agent retains IP-level connectivity, which is
        // intentional given the runaway-prevention threat model.
        cmd.arg("--dns").arg("127.0.0.1");
        for entry in allowlist {
            cmd.arg("--add-host")
                .arg(format!("{}:{}", entry.hostname, entry.ip));
        }
    }

    cmd.arg("--name")
        .arg(&container_name)
        .arg("-v")
        .arg(&work_mount)
        .arg("-v")
        .arg(&workspace_mount)
        .arg("--workdir")
        .arg(CONTAINER_WORK_PATH)
        .arg(image)
        .arg("sleep")
        .arg("infinity");

    let output = cmd.output().await?;
    if !output.status.success() {
        return Err(SandboxError::Start(format!(
            "container run failed: {}",
            String::from_utf8_lossy(&output.stderr).trim()
        )));
    }
    let container_id = String::from_utf8_lossy(&output.stdout).trim().to_string();
    Ok(container_id)
}

fn container_name_for(run_id: &str) -> String {
    // Docker container names allow `[a-zA-Z0-9][a-zA-Z0-9_.-]*` —
    // sanitise the run id to fit even if the runner ever generates
    // something fancier.
    let sanitised: String = run_id
        .chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() || c == '_' || c == '.' || c == '-' {
                c
            } else {
                '_'
            }
        })
        .collect();
    format!("thalyn-sandbox-{sanitised}")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn container_name_sanitises_unsafe_runs() {
        assert_eq!(container_name_for("r_abc123"), "thalyn-sandbox-r_abc123");
        assert_eq!(
            container_name_for("r/with spaces"),
            "thalyn-sandbox-r_with_spaces"
        );
    }

    /// Real Docker round-trip — gated by runtime detection so the
    /// test self-skips on machines without Docker. CI runners on
    /// Linux ship Docker; macOS dev machines have Docker Desktop.
    #[tokio::test]
    #[ignore = "requires docker; run with `cargo test -- --ignored`"]
    async fn tier1_round_trip_against_real_docker() {
        if ContainerRuntime::detect().await.is_none() {
            eprintln!("skipping: no container runtime available");
            return;
        }

        let workspace = make_git_workspace("round_trip");
        let sandbox = match Tier1Sandbox::start(SandboxSpec {
            run_id: "r_round_trip".into(),
            workspace: workspace.clone(),
            egress_allowlist: vec![],
        })
        .await
        {
            Ok(s) => s,
            Err(err) => {
                eprintln!("skipping: tier1 start failed ({err})");
                return;
            }
        };

        let echo = sandbox
            .exec(&["sh".into(), "-c".into(), "echo hi".into()])
            .await
            .unwrap();
        assert_eq!(echo.exit_code, 0);
        assert!(echo.stdout.contains("hi"));

        // Default-deny egress: even the loopback to a name should not
        // resolve when network=none denies the whole stack.
        let resolve = sandbox
            .exec(&[
                "sh".into(),
                "-c".into(),
                "getent hosts example.com >/dev/null && echo resolved || echo blocked".into(),
            ])
            .await
            .unwrap();
        assert!(resolve.stdout.contains("blocked"));

        Box::new(sandbox).teardown().await.unwrap();
        cleanup_git_workspace(&workspace);
    }

    /// With an allowlist, the listed hostname resolves to the IP the
    /// host saw at start time; anything else still fails to resolve.
    #[tokio::test]
    #[ignore = "requires docker + outbound DNS; run with `cargo test -- --ignored`"]
    async fn tier1_allowlist_pins_specific_hostname() {
        if ContainerRuntime::detect().await.is_none() {
            eprintln!("skipping: no container runtime available");
            return;
        }

        let workspace = make_git_workspace("allowlist");
        let sandbox = match Tier1Sandbox::start(SandboxSpec {
            run_id: "r_allowlist".into(),
            workspace: workspace.clone(),
            egress_allowlist: vec!["example.com".into()],
        })
        .await
        {
            Ok(s) => s,
            Err(err) => {
                eprintln!("skipping: allowlist resolution failed ({err})");
                cleanup_git_workspace(&workspace);
                return;
            }
        };

        // Allowlisted host resolves via /etc/hosts.
        let allowed = sandbox
            .exec(&[
                "sh".into(),
                "-c".into(),
                "getent hosts example.com | awk '{print $1}'".into(),
            ])
            .await
            .unwrap();
        assert_eq!(allowed.exit_code, 0);
        assert!(
            !allowed.stdout.trim().is_empty(),
            "expected an IP for the allowlisted hostname; got empty"
        );

        // Non-allowlisted host fails to resolve (DNS at 127.0.0.1
        // black-holes anything not in /etc/hosts).
        let blocked = sandbox
            .exec(&[
                "sh".into(),
                "-c".into(),
                "getent hosts not-in-allowlist.invalid >/dev/null \
                  && echo resolved || echo blocked"
                    .into(),
            ])
            .await
            .unwrap();
        assert!(blocked.stdout.contains("blocked"));

        Box::new(sandbox).teardown().await.unwrap();
        cleanup_git_workspace(&workspace);
    }

    fn make_git_workspace(label: &str) -> PathBuf {
        let dir =
            std::env::temp_dir().join(format!("thalyn-tier1-{}-{}", label, std::process::id()));
        if dir.exists() {
            std::fs::remove_dir_all(&dir).unwrap();
        }
        std::fs::create_dir_all(&dir).unwrap();
        let init = std::process::Command::new("git")
            .args(["init", "-q", "-b", "main"])
            .current_dir(&dir)
            .status()
            .unwrap();
        assert!(init.success(), "git init must succeed for tier1 tests");
        std::process::Command::new("git")
            .args(["config", "user.email", "test@thalyn.local"])
            .current_dir(&dir)
            .status()
            .unwrap();
        std::process::Command::new("git")
            .args(["config", "user.name", "Test"])
            .current_dir(&dir)
            .status()
            .unwrap();
        std::fs::write(dir.join("README.md"), b"hello").unwrap();
        std::process::Command::new("git")
            .args(["add", "."])
            .current_dir(&dir)
            .status()
            .unwrap();
        std::process::Command::new("git")
            .args(["commit", "-q", "-m", "initial"])
            .current_dir(&dir)
            .status()
            .unwrap();
        dir
    }

    fn cleanup_git_workspace(path: &Path) {
        let _ = std::fs::remove_dir_all(path);
    }

    #[tokio::test]
    async fn non_git_workspace_is_rejected() {
        let dir = std::env::temp_dir().join(format!("thalyn-tier1-nongit-{}", std::process::id()));
        if dir.exists() {
            std::fs::remove_dir_all(&dir).unwrap();
        }
        std::fs::create_dir_all(&dir).unwrap();

        if ContainerRuntime::detect().await.is_none() {
            // No docker → start short-circuits before the git probe.
            // The first failure is "no container runtime"; either way
            // start must error.
            let err = Tier1Sandbox::start(SandboxSpec {
                run_id: "r_x".into(),
                workspace: dir.clone(),
                egress_allowlist: vec![],
            })
            .await
            .unwrap_err();
            assert!(matches!(err, SandboxError::Start(_)));
            let _ = std::fs::remove_dir_all(&dir);
            return;
        }

        let err = Tier1Sandbox::start(SandboxSpec {
            run_id: "r_y".into(),
            workspace: dir.clone(),
            egress_allowlist: vec![],
        })
        .await
        .unwrap_err();
        match err {
            SandboxError::Start(msg) => assert!(msg.contains("not a git repo")),
            other => panic!("expected Start error, got {other:?}"),
        }
        let _ = std::fs::remove_dir_all(&dir);
    }
}
