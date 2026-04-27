# ADR-0011 — Sandbox tiers: devcontainer + worktree default; microVM opt-in

- **Status:** Accepted (provisional)
- **Date:** 2026-04-25

## Context

Per `01-requirements.md` F7, agents need isolation for runaway-prevention (not adversarial defense — see ADR-0016 going-public posture). Different tasks have different risk profiles; one-size-fits-all isolation either over-burdens normal tasks or under-protects risky ones.

## Decision

Four-tier model implemented as different `Sandbox` adapters behind one Rust trait:

- **Tier 0 — Bare process.** Read-only mount of workspace. For "summarize this file" sub-agents.
- **Tier 1 — Devcontainer + per-agent git worktree (default).** Container provides process isolation; the worktree is the only writable path; egress is allowlisted per task.
- **Tier 2 — microVM (opt-in).** Firecracker on Linux; Lima on macOS as a bridge until macOS 26 Tahoe ships Apple Containerization (Sept 2026), then upgrade. For executing untrusted/generated code, network-touching automation, or any task the user tags higher-risk.
- **Tier 3 — Cloud sandbox (opt-in).** E2B or Daytona via HTTP. For compute-heavy or GPU work the user prefers off-laptop.

The brain picks the tier when dispatching a sub-agent; the user can override per task or per project.

## Consequences

- **Positive.** Risk-matched overhead. Tier 1 is fast enough to be the default without complaint; Tier 2 is available without requiring the user to opt in to it for every task.
- **Negative.** Four implementations to maintain. Tier 2 on Mac has a moving target (Lima now → Apple Containerization later).
- **Neutral.** Cloud tier is purely opt-in; no one is required to have a Daytona account.

## Alternatives considered

- **Single tier (devcontainer only).** Rejected — under-protects when the user knowingly runs riskier code.
- **Single tier (microVM always).** Rejected — over-burdens normal tasks.
- **Wasm-based sandbox (Wasmtime).** Considered for future; rejected for v1 because tooling and FS/network model don't yet match the agent-execution shape.

## Notes

Tier 2 is the most-likely-to-evolve tier. ADR-update is expected when Apple Containerization replaces Lima on macOS.

### Refinement at v0.6 architecture review

The original ADR estimated Apple Containerization in *"macOS 26 Tahoe (Sept 2026)."* The actual timeline is **earlier**: macOS Tahoe shipped on **September 15, 2025**, and Apple announced both the Containerization framework and the `container` CLI at WWDC 2025. As of this review (2026-04-26), Tahoe is on 26.4.1 and Apple Containerization is generally available.

The four-tier model in **Decision** is unchanged; only the macOS Tier-2 timeline is wrong. v0.7 lands Tier 1 next; v0.15 is when Tier 2 lands, and by then the Mac path should be **Apple Containerization directly**, with Lima as a transitional bridge for users on older macOS or environments where Apple's framework isn't a fit (Lima has also been demonstrated bridging *to* Apple Containerization, so it remains useful as a compatibility layer). Firecracker on macOS via Lima is still available for users who specifically want Firecracker semantics.

No supersession — the decision still holds; the timeline note is correcting a forecast.

### Refinement at v0.12 architecture review

Tier 1 shipped in v0.7 with **Docker + devcontainer + git worktree**
as the implementation. The threat model held: DNS-level egress
allowlist matches the runaway-prevention OQ-2 framing in
`01-requirements.md` §10.1; targeted-attacker defence stays in
the going-public hardening list. The Tier 1 sandbox tests were
rewritten to verify properties from inside the container rather
than reading from the host (Docker Desktop's file-sharing config
varies across hosts).

For Tier 2 on Mac, the path is now **Apple Containerization
directly** rather than via Lima. `apple/container` and
`apple/containerization` are both active GitHub repos, the
framework runs each Linux container in its own lightweight VM
on macOS 26 Tahoe with hardware-level isolation, and exposes a
`SandboxService` actor for managed sandbox operations. Lima
becomes the macOS-fallback path for users on macOS < 26, or
environments where Apple Containerization isn't a fit. Linux
Tier 2 stays Firecracker as the ADR specifies.

This is a continuation of the v0.6 timeline correction, not a
new decision; the Decision section is unchanged.
