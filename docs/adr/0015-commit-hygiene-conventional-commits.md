# ADR-0015 — Commit hygiene: Conventional Commits + git-cliff + leakage scan + no Co-Authored-By

- **Status:** Accepted (provisional)
- **Date:** 2026-04-25

## Context

`01-requirements.md` F12 requires zero internal-workflow leakage in commit messages, no automated `Co-Authored-By: Claude` trailers, and a strong pre-commit review framework. The user has been burned repeatedly by leakage and is emphatic about this. Commits must read as if a human engineer wrote them in the natural course of work.

## Decision

- **Conventional Commits** as the message format (`type(scope): subject`, body explains *why*).
- **`commitlint`** validates message format on commit.
- **`git-cliff`** generates `CHANGELOG.md` from history at release time. Chosen over Node-based alternatives because the project already has a Rust toolchain.
- **Forbidden-token scanner** (`scripts/scan-leakage.sh`) runs as a pre-commit hook *and* as a Claude Code `PostToolUse` hook on `Bash(git commit:*)`. It greps the staged diff and the commit message for forbidden tokens (e.g., `phase`, `v0\.\d`, `prompt plan`, `as instructed`, `Co-Authored-By: Claude`, the `spock` code name). Any hit blocks the commit.
- **`Co-Authored-By: Claude` disabled at the project level** via `.claude/settings.json`:
  ```json
  { "attribution": { "commit": "", "pr": "" } }
  ```

Until Claude Code ships a true `PreCommit` hook, the `PostToolUse` matcher on `Bash(git commit:*)` is the agent-side enforcement layer; the git pre-commit hook is the human-side and CI safety net. Both run, both must pass.

## Consequences

- **Positive.** Three independent layers (git pre-commit, Claude Code hook, CI re-check). Forbidden-token list is editable in one file. `git-cliff` produces a usable `CHANGELOG.md` without a Node dep.
- **Negative.** False positives from the leakage scan are possible (e.g., a legitimate use of "phase" in a non-internal context). The scan should be tuned with surrounding-context heuristics if false positives become annoying.
- **Neutral.** Conventional Commits adds a small per-commit cognitive load that quickly becomes habit.

## Alternatives considered

- **`semantic-release` / `release-please`** (Node-based). Rejected; adds Node toolchain dep when we don't need one.
- **No format enforcement.** Rejected; we want auto-generated changelogs and clear release notes.
- **Disabling the leakage scan in early phases.** Rejected; the failure mode the user has been burned by happens *most often* in early commits, so the scan must be live from v0.1.0.

## Notes

The forbidden-token list lives in `scripts/scan-leakage.sh` (canonical) and is mirrored in `CONTRIBUTING.md` for human reference. Adding a token to the list is a one-line PR.
