# ADR-0021 — Agent hierarchy: brain → leads → sub-leads → workers, persisted as AGENT_RECORD

- **Status:** Proposed
- **Date:** 2026-04-28
- **Supersedes:** —

## Context

The v1 build had two agent kinds: the brain (one) and worker runs
(many). Workers were ephemeral — spawned per task, no identity
beyond `agent_runs.run_id`. v2 introduces a hierarchical management
metaphor (per `01-requirements.md` §F1, §F2): the brain is an
engineering manager named **Thalyn**; each project gets a **lead**;
leads can spawn **sub-leads** for facets of their work; leads (and
sub-leads) dispatch **workers** for individual tasks. The first three
tiers are persistent — they survive restarts, accumulate memory and
relationships, and are addressable in chat ("Lead-Thalyn, status?",
"Lead-Thalyn → SubLead-Harness, what's the latest?"). Workers remain
ephemeral, but they're now spawned by their parent lead rather than
the brain.

This shape is the structural change that makes multi-project Thalyn
tractable. Without persistent leads the brain has to re-load every
project's full context every time focus shifts — an unbounded growth
path that defeats the eternal-session promise. With persistent leads
the brain delegates project-scoped work and only handles routing,
relay, and direct-to-user reasoning.

The architecture doc (`02-architecture.md` §4.2, §5) already places
the agent registry in the brain sidecar and stores it in the
`agent_records` table. ADR-0028 ratified the storage ownership; this
ADR ratifies the *model* the storage represents.

## Decision

Adopt **brain → lead(s) → sub-lead(s) → worker** as the agent
hierarchy, persisted in `agent_records`. The same row shape applies
at every level — the kind column distinguishes them, and
`parent_agent_id` carries the hierarchy.

Concrete shape:

- **Identity.** Each row in `agent_records` has a stable `agent_id`,
  a `kind` from `{brain, lead, sub_lead, worker_persistent}`, a
  `display_name` (user-renameable), an optional `project_id`, an
  optional `parent_agent_id` for the hierarchy, an optional
  `scope_facet` (the slice a sub-lead owns), a `memory_namespace`,
  and a `default_provider_id`.
- **Cardinality and constraints.**
  - Exactly one `kind='brain'` row per install (the canonical
    `agent_brain` seeded in migration 004).
  - At most one `kind='lead'` per project (`project_id` is set;
    `parent_agent_id` is null).
  - Sub-leads have `kind='sub_lead'`, `parent_agent_id` pointing at
    a lead, and inherit the lead's `project_id`.
  - Workers are *ephemeral by default* — they live as
    `agent_runs` rows. A `kind='worker_persistent'` row only exists
    for workers that need to survive across multiple runs (rare;
    deferred until a concrete use case).
  - Hierarchy depth is capped at 2 in v1 (brain → lead → sub-lead).
    Deeper nesting requires the `gateKind='depth'` approval that
    surfaces when a sub-lead tries to spawn another sub-lead.
- **Lifecycle.** Spawn / pause / resume / archive — all transitions
  flip `status` (`active | paused | archived`) and stamp
  `last_active_at_ms`. The lifecycle code itself lands when the
  lead-as-first-class stage adds it; the data shape is in place from
  v0.20.
- **Memory namespacing.** Each agent's `memory_namespace` is a
  composite key used by the memory layer to scope reads / writes:
  brain memory is global, project memory is keyed to the project's
  lead, sub-lead memory is keyed to the sub-lead's namespace and
  inherits read-only access to the parent lead's namespace. Memory
  isolation between sibling sub-leads is enforced at the storage
  layer, not just the API layer.
- **Default provider.** Each agent has a `default_provider_id`. The
  brain inherits the user's chosen brain provider; leads default to
  the same; workers go through the routing layer (per ADR-0023).
- **Addressability.** Leads and sub-leads are user-addressable in
  chat by `display_name` (with `Lead-` / `SubLead-` prefixes by
  default; user-renameable). The brain is always **Thalyn** unless
  the user renames it.
- **`AGENT_RUN` linkage.** Every run row has an optional `agent_id`
  (which agent is *running*) and optional `parent_lead_id` (which
  lead spawned this run). v1 runs are migrated under
  `parent_lead_id = agent_lead_default` (per migration 004); future
  runs populate `agent_id` per the spawning agent.
- **Sanity-check critic at every hop.** Lead replies pass through a
  sanity-check critic before reaching the brain (per F1.8 / F12.7);
  the brain's relayed text passes through a `relayed_vs_source` critic
  before reaching the user. The critic itself lives in the drift
  monitor (existing v1 primitive, generalized in the
  information-flow-drift stage); this ADR records the *positions* in
  the hierarchy where the critic runs.

## Consequences

- **Positive.**
  - **Same primitive at every level.** A lead and a sub-lead are
    both `agent_records` rows with different `parent_agent_id` and
    `kind`. Code that operates on agents doesn't branch on kind
    except where the kind genuinely matters (memory namespacing,
    routing fallback). Adding deeper hierarchy in the future is an
    enum extension, not a rewrite.
  - **Persistent identity is addressable.** Direct lead chat
    becomes a first-class surface (in the lead-chat stage); the user
    can talk to a specific lead by name at any time without losing
    the eternal thread.
  - **Multi-project scales.** Brain delegates project-scoped work to
    leads; leads carry their own memory namespace; the eternal
    thread doesn't have to re-load everything on every focus shift.
- **Negative.**
  - **Memory namespacing complexity.** Five tiers (working /
    session / project / personal / episodic / agent) plus per-agent
    namespaces means careful test coverage at the storage layer.
    The memory-closure stage tightens this; bypass-the-API tests
    confirm isolation.
  - **Sub-lead depth-cap is a UX decision, not a technical one.**
    If users routinely override the cap, the cap is wrong. Re-evaluate
    at the next architecture review after the sub-lead stage lands.
- **Neutral.**
  - **Naming convention.** `Lead-<Project>` and `SubLead-<Facet>`
    are the defaults; user-renameable. The conversation feels right
    when the names feel like people, not roles — empirical tuning.
  - **Worker persistence is deferred.** v1 workers stay ephemeral.
    `kind='worker_persistent'` exists in the schema for the cases
    that emerge later (long-running observers, drift monitors); the
    lifecycle for them lands when a concrete use case forces it.

## Alternatives considered

- **Single agent kind with role-typed metadata.** Rejected: code
  that operates on agents needs to know the role to apply the right
  memory namespace, routing fallback, and sanity-check policy.
  Embedding role in metadata pushes the dispatch into ad-hoc
  string checks. The kind enum is the readable, type-safe form.
- **Brain → worker only (defer leads).** Rejected: leads are the
  load-bearing piece for multi-project. Without them, every focus
  shift rebuilds the brain's working context from scratch — the
  eternal-session promise's failure mode.
- **Lead per agent run (ephemeral leads).** Rejected: a lead's
  value is *accumulated context* (memory, relationships, patterns).
  An ephemeral lead loses its defining property. The persistent
  identity is what makes the EM metaphor honest.
- **Tree of arbitrary depth from day one.** Rejected: the depth-2
  cap is a safety net, not a permanent ceiling. Removing it before
  we have data on how users use sub-leads invites
  deep-nesting failure modes (long agent chains, untraceable
  attribution, runaway autonomy).

## Notes

This ADR is **drafted now** with `Status: Proposed` even though the
lifecycle work (spawn / pause / resume / archive transitions, the
direct-lead-chat surface, sub-lead spawning) lands in subsequent
stages. The data model is the artefact this ADR explains, and that
data model is what migration 003 + the agents/projects stores set up.
The status flips to `Accepted` when the lead-as-first-class stage
ratifies it under load.

`02-architecture.md` §4.2, §5 (data model) and §13 (risk #4 —
LangGraph + Claude Agent SDK session-id coupling) are the touchpoints
this ADR builds on.
