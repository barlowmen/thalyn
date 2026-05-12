# ADR-0030 — License: Apache-2.0

- **Status:** Accepted
- **Date:** 2026-05-12
- **Deciders:** Barlow
- **Supersedes:** [ADR-0016](0016-license-mit.md)
- **Superseded by:** —
- **Refines:** —

## Context

ADR-0016 chose MIT as a provisional v0.x license, with an explicit
note that the choice would be revisited before the source became
publicly visible. The trigger for the revisit has now arrived:
making the GitHub repository public so that CI runs against the
free-tier minutes available to public repos. Reading the source
under MIT vs Apache-2.0 doesn't change anything for a single user
running the binary, but it does change the protection profile for
contributors and downstream users in the broader space the project
operates in.

The relevant facts at decision time:

- The project sits in the AI / agent-orchestration space, where
  patent activity is increasing (Anthropic, OpenAI, several
  startups have all filed patents on agent topologies, prompt
  techniques, sandboxing patterns, drift-monitoring approaches).
  Apache-2.0's §3 (explicit patent grant) and the matching
  termination-on-suit clause are real protection here; MIT is
  silent on patents.
- The author is the sole copyright holder. Re-licensing is
  unilateral until any external contributor lands code under the
  current license, after which it requires their consent.
- The most-relied-on parts of the upstream stack are mixed:
  Tauri is dual MIT/Apache, LangGraph is MIT, the Anthropic SDK
  is MIT. Both license families are consumable.
- The repo is moving to public visibility *before* v1.0 ships.
  The going-public-checklist's "re-evaluate license" item
  (originally framed as a v1.0-distribution gate) is therefore
  brought forward into this decision.

## Decision

**Switch from MIT to Apache-2.0** in the same commit that publishes
the repo's `LICENSE` change. The repo ships:

- `LICENSE` — verbatim Apache-2.0 text (Apache Software Foundation,
  January 2004).
- `NOTICE` — short attribution per Apache-2.0 §4(d) convention.
- The README's License section names Apache-2.0 and points at
  `LICENSE` and `NOTICE`.

ADR-0016 is marked Superseded by this ADR.

Per-file Apache-2.0 headers are **not** added. The repo-root
`LICENSE` file is sufficient by Apache-2.0's terms; per-file headers
are recommended but not required, and the maintenance burden does
not pay for itself at the project's current size.

## Consequences

- **Positive.** Contributors and users get Apache-2.0's explicit
  patent grant and termination-on-suit clause. The license decision
  is closed, removing one item from the going-public-checklist.
- **Positive.** Re-licensing happened while the author was the
  sole copyright holder, so no contributor-permission round-trip
  was needed. Future external contributions land under
  Apache-2.0 from day one.
- **Neutral.** Apache-2.0 is mainstream in this space; downstream
  consumers familiar with it have no friction. Mixing with MIT
  upstream dependencies is fine — both licenses combine without
  issue.
- **Negative.** Slightly more text in the `LICENSE` file (~200
  lines vs MIT's ~20). Distribution archives carry both `LICENSE`
  and `NOTICE` rather than just `LICENSE`.

## Alternatives considered

- **Stay on MIT.** Defensible — the patent risk is theoretical for
  a project that hasn't shipped binaries to anyone but the author.
  Rejected because the unilateral-re-license window narrows the
  moment any external contributor lands a PR; the cost of
  switching now is hours, the cost of switching later (once
  contributors exist) is contributor consent for every file the
  author no longer solely owns. Switching pre-emptively is
  cheaper.
- **Dual-license MIT + Apache-2.0** (Tauri's pattern). Rejected:
  doubles the LICENSE-related surface for marginal benefit at
  this scale. A single mainstream license is simpler.
- **MPL-2.0 / GPL.** Rejected for the same reason ADR-0016
  rejected them — copyleft is too restrictive for a tool meant
  to be embedded and customized.
- **Apache-2.0 with per-file headers.** Rejected for v0.x scope.
  The repo-root `LICENSE` satisfies the license; per-file headers
  add a maintenance tax (every new file gets a header, the lint
  gate has to enforce it) for a benefit that mostly accrues when
  files are excerpted out of the repo. Revisit if that becomes a
  real distribution pattern.

## Notes

- The license switch lands in one commit alongside ADR-0016's
  status update so the supersede relationship is captured atomically.
- The `NOTICE` file is intentionally minimal (project name +
  copyright + pointer to `LICENSE`). It can grow if/when the project
  starts vendoring third-party code that carries its own attribution
  notices the Apache-2.0 §4(d) clause requires reproducing.
