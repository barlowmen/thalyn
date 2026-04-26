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

## Tier 2 — microVM (opt-in, deferred)

Reserved for tasks that execute generated code, run untrusted
dependencies, or that the user has explicitly tagged higher-risk.
Implementation per ADR-0011:

- **Linux:** Firecracker.
- **macOS:** Apple Containerization (generally available since
  macOS Tahoe, September 2025); Lima as a transitional bridge for
  older macOS releases or environments where the Apple framework
  isn't a fit.
- **Windows:** TBD — no v1 commitment.

Scheduled to land in **phase v0.15**. Today, requesting Tier 2
returns a clear "not implemented" error from the Rust core's
sandbox manager.

## Tier 3 — cloud sandbox (opt-in, deferred)

Off-laptop execution via E2B or Daytona for compute-heavy or
GPU-bound work. Opt-in with the user's own API key — Thalyn never
proxies through a Thalyn-operated server.

Scheduled to land in **phase v0.15** alongside Tier 2.

## Selection guidance

| Task profile | Tier |
|---|---|
| "Read this file and summarise" | Tier 0 |
| "Edit a file in the repo" | Tier 1 (default) |
| "Run the test suite" | Tier 1 |
| "Browse the web for context" | Tier 1 + egress allowlist |
| "Execute model-generated code" | Tier 2 (when available) |
| "Train a model" | Tier 3 (when available) |

The user can override the planner's tier per task or per project.
The override surface is part of the v0.x.y settings UX work that
follows once Tier 2/3 land.

## Restricted shell

Sub-agents reach the host shell only through the `restricted_shell`
tool, which gates calls behind a binary allowlist plus a short
catastrophic-shape pattern blocklist. The canonical lists live in
[`docs/sandbox-shell-allowlist.md`](sandbox-shell-allowlist.md).
