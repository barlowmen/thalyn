# Sandbox tiers

Thalyn confines sub-agent work in a tier whose isolation overhead is
proportional to the task's risk. The tier is chosen per task at
plan time — the planner emits `sandbox_tier` on each delegated step
— with sensible defaults so most agents never need to think about
the choice.

> Per [`01-requirements.md`](../01-requirements.md) §10.1 OQ-2 and
> [ADR-0011](adr/0011-sandbox-tiers-devcontainer-microvm.md), v1
> defends against **runaway agents** and **prompt-injection-induced
> runaway behaviour**, not against targeted attackers. The tier
> labels reflect that scope.

## Tier 0 — bare process

Read-only access to a workspace. The "container" is just the host
process; nothing is isolated. Used for "summarise this file" or
"grep for X" sub-agents where overhead matters more than
confinement.

- **Filesystem:** workspace mounted read-only by convention.
- **Network:** host network (Tier 0 does not enforce egress).
- **Spawn cost:** zero — no container, no VM.
- **Use when:** a sub-agent needs to inspect files but won't write
  or shell out.

## Tier 1 — devcontainer + git worktree (default for delegated steps)

The default. Each spawn carves a fresh git worktree under
`<repo>/.thalyn-worktrees/<run_id>`, mounts it writable at `/work`
inside a Docker container, and re-mounts the original workspace
read-only at `/workspace-ro` so reference files are still readable.
The container starts in `--network=none` (hard-deny egress) and
tightens to a [DNS allowlist](#egress-allowlist) when the spec
lists permitted hostnames.

- **Filesystem:** writable worktree at `/work`, read-only mirror of
  the workspace at `/workspace-ro`.
- **Network:** default-deny; per-task DNS allowlist when configured.
- **Spawn cost:** one `docker run` (~100 ms with a warm image).
- **Use when:** a sub-agent needs to write files, run shell
  commands, or otherwise touch the system. This is the right
  default for almost every delegated step.

### Egress allowlist

With an empty allowlist the container has no network at all
(`--network=none`). With a non-empty allowlist:

1. The host resolves each hostname once at start time.
2. The resulting `host:ip` pairs are injected via Docker's
   `--add-host` flag, populating `/etc/hosts` inside the container.
3. Container DNS is pointed at a black-hole resolver (`--dns 127.0.0.1`)
   so non-allowlisted name lookups fail.

This is **DNS-level enforcement**: a runaway agent that already
knows an IP could still reach it. The threat model in v1 is
runaway prevention, not adversarial defence — that's an explicit
choice on the going-public hardening list.

### Worktree lifecycle

The worktree is created via `git worktree add --detach` so any
commits the sub-agent makes land on a detached branch the user
can merge or discard. On teardown, `git worktree remove --force`
prunes the worktree directory; the parent repo's history is
untouched.

The repo *must* be a git repo for Tier 1 to start. Non-git
workspaces fall through to Tier 0.

## Tier 2 — microVM (opt-in)

Reserved for tasks that execute generated code, run untrusted
dependencies, or that the user has explicitly tagged higher-risk.
Two backends share one trait:

- **Linux: Firecracker.** Started via the `firecracker` CLI; workspace
  is mounted via virtio-fs; networking uses a tap device with the same
  default-deny posture as Tier 1.
- **macOS: Lima.** Bridge until macOS 26 Tahoe ships Apple
  Containerization (per ADR-0011). Started via `limactl`; workspace
  is mounted via reverse-sshfs.
- **Windows:** no v1 commitment.

The trait surface, detection, and per-backend error mapping all
ship in v0.15. The actual VM image lifecycle (kernel + rootfs
provisioning, virtio-fs share, tap-device networking) is the
v0.15.x follow-up — there's no clean user-facing image-management
story to commit to in v0.15 that doesn't lock in long-term
decisions about where images live, which kernels we ship, and how
the user provisions a fresh sandbox.

Until image management lands, **`Tier2Sandbox::start` surfaces a
typed `ImageProvisioningPending` error**. The escalation policy
treats this as fall-back-to-Tier-1 with a warning, so a request
for Tier 2 isolation never silently runs without isolation: the
agent stays in Tier 1 (still confined; still default-deny network)
and the action log carries the `Requested tier_2 but it is not
available on this host; falling back to tier_1` warning.

## Tier 3 — cloud sandbox (opt-in)

Off-laptop execution via E2B or Daytona for compute-heavy or
GPU-bound work. Opt-in with the user's own API key — Thalyn never
proxies through a Thalyn-operated server.

The user pastes their key in Settings → Observability (the panel is
named for the cluster of opt-in cloud features rather than the
strict telemetry meaning); the Rust core forwards it to the brain
via env var. Detection reads `THALYN_E2B_API_KEY` /
`THALYN_DAYTONA_API_KEY` at runtime, with E2B taking precedence
when both are set.

The HTTP integration to either provider is the v0.15.x follow-up;
the trait shape, detection, and escalation fall-back are stable
today.

## Escalation policy

The planner annotates each plan node with an optional
`sandbox_tier` hint. The brain's escalation module
(`thalyn_brain.orchestration.escalation`) resolves the *effective*
tier through three lenses:

1. **Auto-escalation.** A plan node whose description or rationale
   contains one of the high-risk hints — "execute generated code,"
   "untrusted dependencies," "install from URL," etc. — gets bumped
   to Tier 2 even if the planner asked for Tier 1.
2. **User override.** A persisted setting can set a floor ("at
   least Tier 1"), a ceiling ("never Tier 3"), or an absolute
   override. Applied after auto-escalation so the human always wins.
3. **Availability fallback.** If the resolved tier isn't installed
   (no Firecracker, no API key, image provisioning still pending),
   the policy falls **down** to the strongest available tier at or
   below the request, with a warning that surfaces in the action
   log. We never silently *strengthen* isolation past the user's
   request — stronger isolation can break tools the agent expected
   to have.

## Selection guidance

| Task profile | Tier |
|---|---|
| "Read this file and summarise" | Tier 0 |
| "Edit a file in the repo" | Tier 1 (default) |
| "Run the test suite" | Tier 1 |
| "Browse the web for context" | Tier 1 + egress allowlist |
| "Execute model-generated code" | Tier 2 (auto-escalated; falls back to Tier 1 with a warning until image management lands) |
| "Run a long benchmark" | Tier 3 (with API key) |
| "Train a model" | Tier 3 (with API key) |

The user can override the planner's tier per task or per project
via `EscalationInput.user_override` / `user_floor` / `user_ceiling`.
The settings-panel surface for these knobs is the v0.x.y work that
rides into the next polish phase.

## Restricted shell

Sub-agents reach the host shell only through the `restricted_shell`
tool, which gates calls behind a binary allowlist plus a short
catastrophic-shape pattern blocklist. The canonical lists live in
[`docs/sandbox-shell-allowlist.md`](sandbox-shell-allowlist.md).
