# ADR-0031 — Repo public: source visibility precedes app distribution

- **Status:** Accepted
- **Date:** 2026-05-12
- **Deciders:** Barlow
- **Supersedes:** —
- **Superseded by:** —
- **Refines:** ADR-0016 (via ADR-0030's license switch)

## Context

Two facts forced the decision:

1. **Free CI matters for an actively-developed pre-alpha.** GitHub
   Actions on private repos is metered against a paid quota that
   the project's CI volume (every push gets the full gate sequence
   on every job) consumes faster than is comfortable. Public repos
   get free Actions minutes for the same workloads. The project
   has no other compelling reason to remain private.
2. **The going-public-checklist (`docs/going-public-checklist.md`)
   was written assuming "going public" was one event** — source +
   binary together. That assumption is wrong. Reading the source
   on GitHub doesn't move the threat-model boundary; running an
   installer the project distributes does. The two events can be
   decoupled.

Pre-flip readiness was confirmed by a survey of the repo: no
secrets in git history (the `git log --all -p` filter for the
high-confidence patterns came up clean), no CI workflow secrets that
would break fork PRs (zero `${{ secrets.* }}` references in
`.github/workflows/`), all required public-facing files in place
(LICENSE, NOTICE, SECURITY.md, CODE_OF_CONDUCT.md, CONTRIBUTING.md,
README), no maintainer-personal-info leaks beyond a single Storybook
default-arg path that was fixed pre-flip.

## Decision

**Flip the GitHub repository to public visibility.** Source becomes
readable / cloneable / forkable by anyone. The ApplicationID, the
binary distribution, the threat-model expansion, and the rest of
the going-public-checklist remain gated by their existing items.

Sequenced changes in support of the flip, all landed before the
visibility switch:

- **License: Apache-2.0** (ADR-0030, supersedes ADR-0016).
  Apache-2.0's explicit patent grant matters as soon as the source
  is visible to anyone who might also hold relevant patents; MIT
  is silent on patents.
- **Project planning docs (`01-requirements.md`,
  `02-architecture.md`, the build plan) stay private.** They live
  on the maintainer's local filesystem, are gitignored in the
  public repo, and are removed from git history via
  `git filter-repo`. The public design record is the ADR set
  (`docs/adr/`), the architecture-review summaries
  (`docs/architecture-reviews/`), and the going-public-checklist
  (`docs/going-public-checklist.md`). ADRs continue to cite the
  requirements / architecture docs by section number; those
  citations are dangling references for public readers and that
  is acceptable — the leak is "an internal doc with a section
  numbered F2.3 exists," which reveals essentially nothing.
- **README + going-public-checklist + CONTRIBUTING reframed.**
  The status banner makes the source-public-but-app-not split
  explicit; the checklist's header clarifies that it gates app
  distribution, not source visibility; CONTRIBUTING sets the
  external-contributor cadence ("issue first, slow review") and
  documents the leakage scanner's forbidden-token list so PR
  authors don't trip it innocently.
- **`.github/ISSUE_TEMPLATE/`** added. Disables blank issues;
  contact links route readers to the README's status section
  before they file.
- **History rewrite** strips the three planning docs and the
  archived prior vision doc from every blob in every commit, plus
  scrubs literal occurrences of the build-plan filename from
  non-planning files' history via `--replace-text`. Tags re-anchor
  onto the new SHAs.

## Consequences

- **Positive.** Free CI on every push and pull request from forks
  (with maintainer approval for first-time-contributor runs).
- **Positive.** The license decision (ADR-0030) closes one item
  on the going-public-checklist before v1.0.
- **Positive.** The "source open under MIT-then-Apache" intent has
  been declared since the project's earliest commit; making it
  observable matches the stated intent.
- **Neutral.** ADRs and code comments retain dangling references
  to the now-private planning docs. The README explains the doc
  layout; readers who want more design context have the ADRs and
  architecture-review summaries.
- **Neutral.** The maintainer's local working tree continues to
  carry the planning docs at the same paths (now gitignored), so
  every existing local workflow and agent harness keeps working
  unchanged.
- **Negative — minor.** External contributors hitting the
  leakage-scanner's forbidden-token list with phrases that read
  natural to them (numbered phases, numbered iterations) will see
  a CI failure they don't immediately understand. CONTRIBUTING.md
  now documents the list explicitly; that's the mitigation.
- **Negative — load-bearing.** Adversaries can now study the code
  for vulnerabilities. This matters when the binary ships, not
  while only the maintainer runs it. The going-public-checklist's
  threat-model items still gate that.

## Alternatives considered

- **Stay private; pay for CI.** Defensible; just costs money. The
  project has no commercial backing and the maintainer prefers not
  to pay for what is structurally a hobby-scale workload. Rejected.
- **Move the source to GitLab / SourceHut.** Both offer free CI
  for private repos. Rejected: would lose the existing GitHub
  history, issues, and tag namespace, and would fragment the
  project's discoverability without a corresponding benefit.
- **Make the source public but keep planning docs in the public
  repo.** Considered. Rejected because the planning docs reveal
  more than the maintainer is comfortable making public —
  pre-shipping product decisions, sequencing, trade-offs that
  benefit from being decided privately before being announced
  publicly. ADRs are a suitable public-facing alternative for
  the *decisions*; the underlying *deliberation* stays private.
- **Public repo with planning docs scrubbed only by `.gitignore`,
  no history rewrite.** Rejected. `.gitignore` only stops future
  changes from being tracked; the historical blobs remain readable
  via `git log -p`. Only `git filter-repo` actually removes them
  from public visibility.
- **Move planning docs to a separate private repo, with symlinks
  back into the public worktree.** Considered. Adds operational
  overhead (two repos to keep in sync) for marginal benefit.
  Gitignore-in-place serves the same purpose with less moving
  parts; if the maintainer later wants version history on the
  planning docs, that decision can be made then.

## Notes

- The flip was performed by the maintainer via the GitHub repo
  Settings → General → Danger Zone → Change visibility flow.
  Branch protection on `main` was tightened in the same session
  (require status checks, require signed commits, no force pushes
  for non-maintainers); fork-PR approval was set to require
  maintainer approval for first-time contributors.
- The going-public-checklist's "re-evaluate license" item is now
  closed (Apache-2.0 chosen). All other items remain open and
  continue to gate the v1.0 binary release.
- Future re-evaluation cycles should treat repo-public as an
  unchanged baseline, not a recent event. The next cycle attaches
  to v0.38 (the polish phase) per the existing cadence.
