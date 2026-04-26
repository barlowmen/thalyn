# ADR-0016 — License: MIT (revisit before public)

- **Status:** Accepted (provisional)
- **Date:** 2026-04-25

## Context

Thalyn is open source, single-user-per-install, with a known intent to become public-facing eventually. License choice now is bounded by: (a) the license of the stack we build on (mostly MIT), (b) the user's preference for low friction, (c) the patent-grant value of Apache-2.0 once the project is published broadly.

## Decision

**MIT license** at v0.x. The repo ships an `LICENSE` file and a `LICENSE` reference in `README.md` and every source file header where the project chooses to use one.

The choice is **revisited before the repo is published publicly** as part of the going-public checklist (`docs/going-public-checklist.md`). Apache-2.0 is the most likely re-choice at that point (explicit patent grant matters for a project that touches sandboxing, agent orchestration, browser control, and scheduling — all patent-able territory).

## Consequences

- **Positive.** Lowest re-use friction. Aligns with the rest of the stack (React, Vite, shadcn/ui, Tauri, LangGraph are MIT). Familiar to every contributor.
- **Negative.** No explicit patent grant — pre-public this is fine; post-public it's a real consideration.
- **Neutral.** Re-licensing from MIT to Apache-2.0 is straightforward (every contributor's MIT contribution is also Apache-2.0-compatible) — no contributor-permission round-trip required.

## Alternatives considered

- **Apache-2.0 from day one.** Rejected for now; revisit pre-public.
- **MPL-2.0 / GPL.** Rejected; copyleft is too restrictive for a tool meant to be embedded and customized.
- **Dual license.** Rejected; unnecessary complexity for a single-developer project.

## Notes

The repo's first commit ships `LICENSE` (MIT). Any later license change is its own ADR (supersedes this one).
