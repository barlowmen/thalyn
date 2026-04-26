# ADR-0014 — Documentation: MADR + Mermaid C4 + ARCHITECTURE.md (+ Astro Starlight later)

- **Status:** Accepted (provisional)
- **Date:** 2026-04-25

## Context

The project needs durable architecture documentation that survives Claude Code-driven development without going stale, renders cleanly on GitHub, is easy to extend, and is friendly to both humans and LLM agents reading or updating it.

## Decision

Three layers:

1. **`ARCHITECTURE.md`** — top-level living overview using **Mermaid C4 diagrams** (context / container / component) for system shape. Updated as part of any change that crosses a component boundary.
2. **`docs/adr/`** — **MADR-format** Architecture Decision Records, one decision per file, immutable once accepted, superseded by new ADRs that link back. Filenames `NNNN-short-slug.md`.
3. **User-facing documentation site** — **Astro Starlight** (deferred to a later phase; see notes). Until then, `docs/` plus a basic `mkdocs serve` works.

Diagrams as Mermaid (renders on GitHub), not Structurizr or PlantUML; agents update Mermaid more reliably than DSLs.

## Consequences

- **Positive.** Everything lives in the repo, in markdown. No third-party services. Mermaid + MADR are agent-readable and agent-writable. ARCHITECTURE.md is the one document a new contributor reads first; the ADR set is the chronological record.
- **Negative.** Mermaid has limits — complex diagrams may need D2 or a real diagramming tool. We accept that boundary; complex diagrams should usually be broken into smaller ones.
- **Neutral.** A docs site can be added at any time; defer until v0.13 or so.

## Alternatives considered

- **arc42 template** — heavier, more prescriptive; rejected for our scope.
- **Structurizr DSL** — strong C4 fidelity, but adds a CLI/build dep and a non-markdown source. Rejected for agent-friction.
- **Notion / Confluence** — rejected; out-of-repo docs go stale fastest.
- **Docusaurus** — heavier than Astro Starlight in 2026; defer if/when we want a docs site.

## Notes

A `docs/architecture-reviews/` directory (not yet created) houses the per-release architecture review artifacts (`01-requirements.md` F12.4).
