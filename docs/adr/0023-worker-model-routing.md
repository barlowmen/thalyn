# ADR-0023 — Worker model routing: task-tag → provider with per-project overrides

- **Status:** Accepted
- **Date:** 2026-04-29 (Proposed) · 2026-04-29 (Accepted)
- **Deciders:** Barlow
- **Supersedes:** —
- **Superseded by:** —

## Context

Workers carry most of the token volume in a busy day (`01-requirements.md`
§F4.6, `02-architecture.md` §7.4): a lead spawning a refactor worker,
an image-generation worker, a research worker, and a quick-answer worker
inside a single user turn is a normal shape. Picking one provider for
every worker — the v1.x default of "everything goes to the brain's
provider" — wastes capability fit (cheap quick-answer tasks pay
frontier-cloud rates; image generation can't use a text-only provider
at all) and surrenders the user's lever on cost vs. quality per task.

The v2 architecture calls for a routing layer keyed on a small task-tag
vocabulary, with per-project user overrides and a global default table
shipped with the app. F4.6 states the v1 baseline explicitly: *every
tag → Opus 4.7*. The mechanism ships before the routing decisions do —
real per-tag tuning waits for usage data post-launch.

Three requirements interlock:

- The routing decision is **per worker spawn**, not per-run or
  per-project. A lead's run can spawn a coding worker and an image
  worker in sequence; each goes through the layer independently.
- The lead is responsible for **tagging** the work it spawns. A worker
  that arrives untagged uses the project's `default` tag.
- A project's `local_only` flag (F3.8) is a **privacy invariant**, not
  just a routing preference. When set, the routing layer is overridden
  to local providers for the entire project's worker fleet, and the
  spawn path refuses cloud providers as belt-and-braces.

The routing-overrides table (`02-architecture.md` §5) and the
`AGENT_RUN.task_tags_json` column landed in v0.20. v0.24 lights up the
mechanism on top of them.

## Decision

A pure `route_worker(task_tag, project_overrides, project_local_only,
global_defaults) → RouteDecision` function in the brain sidecar, called
at worker dispatch time inside the runner's `_spawn_subagent`. The
function does no IO; the caller (the runner) supplies the per-project
overrides loaded from `routing_overrides`, the project's `local_only`
flag loaded from `projects`, and the built-in global defaults shipped
in code. Side effects (audit-log entry, secrets-adapter refusal,
agent_run tagging) live at the call site, not inside the routing
function.

Concrete shape:

- **Task-tag vocabulary.** A small built-in set: `default`, `coding`,
  `image`, `research`, `writing`, `quick`. Six tags is enough headroom
  for v1 — when a real use case forces a seventh, add it. Tags are
  lowercase strings; unknown tags fall through to the `default` route.
  The lead is responsible for setting the tag on each plan node it
  spawns; the planner inherits a default of `default` so legacy plans
  keep routing through the existing path.
- **Resolution order.** `project_local_only` short-circuits to a local
  provider lookup (`mlx` on Apple Silicon, `ollama` elsewhere). If the
  project carries a non-`local_only` override for the tag, the override
  wins. Otherwise the global default table answers, with `default` as
  the fallback when the tag is unknown.
- **Route target.** The function returns a registry key
  (`provider_id`) — `"anthropic"`, `"ollama"`, `"mlx"`,
  `"openai_compat"`, `"llama_cpp"`. The model dimension stays at the
  provider's default for v1; per-model routing can extend the schema
  with a nullable `model` column when usage data shows it's needed
  (deferred per F4.6's "tune from real data" framing).
- **Global defaults.** Built into the brain sidecar in code, not in
  a database table — there is no per-install variation in the v1
  baseline beyond what overrides express. The defaults table maps
  every supported tag to `"anthropic"` for v1; the model swap to Opus
  4.7 is a provider-default change, separate from routing infra. The
  `default` key carries the "no tag specified" fallback.
- **Audit shape.** Each spawn appends an `action_log` entry with
  `kind="decision"` and payload `{action: "route_worker", taskTag,
  projectId, providerId, matched: "override"|"global"|"local_only"}`.
  This lands alongside the per-run NDJSON audit log so the decision is
  inspectable from `runs/{run_id}.log` and queryable across runs from
  `app.db`.
- **`local_only` belt-and-braces.** The runner's spawn path asserts
  the chosen provider's `capability_profile.local` is true for
  `local_only` projects, regardless of how it got chosen. A cloud
  provider sneaking through (e.g., a stale override left over from
  before the flag flipped) raises a `LocalOnlyViolation` that the
  caller logs and surfaces as a run-status `errored` rather than
  silently leaking project data to a cloud token.
- **Conversational edit path.** A small intent parser inside
  `thread.send` recognizes a focused set of phrasings ("route X to Y in
  this project", "make this project local-only") and dispatches to the
  routing-actions module that wraps `RoutingOverridesStore` and
  `ProjectsStore`. The parser is regex-based today; the LLM tool-use
  path lands when the action registry (v0.32) materializes.

The IPC surface — `routing.get`, `routing.set`, `routing.clear` — is
the programmatic edit path. The conversational path is sugar on top of
the same actions; both write through `RoutingOverridesStore`.

## Consequences

- **Positive.**
  - **Mechanism ships before decisions.** Shipping with everything
    routed to one provider means we collect usage data against a
    consistent baseline. Per-tag tunings can land later as data
    arrives without infrastructure churn.
  - **Pure function is testable.** The route lookup has no IO; tests
    cover the resolution-order matrix without spinning up SQLite or
    a registry.
  - **Privacy invariant has two layers.** Routing won't pick a cloud
    provider for `local_only`, and the spawn path asserts the
    invariant even if it did. The "secrets adapter refusal" framing
    in F3.8 is upheld.
  - **Recursive case stays simple.** When sub-leads land in v0.34,
    they spawn workers through the same routing layer — there is no
    sub-lead-specific code path.
- **Negative.**
  - **Provider-level granularity.** "Route coding to Sonnet 4.6"
    can't be expressed today; the user can only swap the provider
    behind that tag. Per-model routing is a schema extension when the
    use case forces it.
  - **Tag vocabulary needs gardening.** Six tags is intentionally
    small; the cost is that planners have to fit work into a coarse
    bucket. Adding tags is cheap, but the temptation to add many
    (and lose the routing-table's legibility) needs discipline.
  - **Conversational path is a regex parser.** Recognizes a handful
    of phrasings; misses anything outside the matched patterns.
    Acceptable for v1 because the IPC path covers the gap; tightens
    when the action registry lands.
- **Neutral.**
  - **Global defaults in code, not DB.** Every install gets the same
    baseline. If we ever want per-org or per-tenant defaults, the
    table is one migration away.
  - **Audit-log entry is a `decision`, not a new kind.** Routing
    decisions ride the existing `decision` action kind to avoid
    fragmenting the audit vocabulary. Filterable by the
    `action: "route_worker"` payload field.

## Alternatives considered

- **Configurable routing in code, no DB at all.** Rejected: the F4.6
  promise is *user-editable per-project tables*. Storing overrides in
  config files would push the surface out of the in-app world the
  hard rule (no external apps) requires.
- **One central rule engine with arbitrary predicates.** Rejected:
  the value of routing is in being legible at a glance ("tag goes
  to provider"). A predicate engine optimizes for expressivity over
  legibility — wrong trade-off for v1.
- **Compose routing into the provider abstraction itself.** Rejected:
  the provider abstraction (ADR-0012) is per-call and unaware of
  project context. Routing is the call-site decision *of which
  provider*, not a property of any provider. Keeping them separate
  preserves the trait's narrowness.
- **Make `task_tag` a freeform string with no vocabulary.** Rejected:
  freeform tags fragment the routing table — every typo becomes a
  new tag with no override. The small enum keeps the table dense and
  the global defaults complete.
- **Skip routing in v1; ship "everything → brain provider".**
  Rejected: by the time real per-tag tuning becomes a priority, the
  spawn path is woven into more code (sub-leads, schedulers, drift
  monitor). Adding routing later is a refactor; adding it now is a
  layer.

## References

- `01-requirements.md` §F4.6 (worker model routing), §F3.8
  (`local_only` privacy carve-out), §F4.2 / §F4.3 (auth-backend split,
  per-tier provider).
- `02-architecture.md` §7.4 (worker model routing pseudocode),
  §5 (`routing_overrides` table, `AGENT_RUN.task_tags_json`).
- ADR-0012 (provider abstraction; the trait this routing layer
  selects between).
- ADR-0021 (agent hierarchy; `default_provider_id` on `agent_records`
  is the lead-tier default that workers route *off of*).
- ADR-0028 (brain owns SQLite storage; routing-overrides table lives
  in the brain's `app.db`).

## Notes

Drafted with `Status: Proposed` alongside the routing-table module
and routing.* IPC. Flipped to `Accepted` at the end of this stage's
work — every load-bearing claim has been exercised under traffic:

- `route_worker` answers the resolution-order matrix (no overrides
  → global default; per-project override; ``local_only``
  short-circuit) under unit + integration coverage.
- `StoreBackedWorkerRouter` reads overrides + the project's
  `local_only` flag from SQLite per spawn and runs the pure
  resolver; the runner consults it in `_spawn_subagent` so the
  child run uses the routed provider, the per-run audit log
  records the `decision` line with `(taskTag, projectId,
  providerId, matched)`, and a `run.routing_decision` notification
  carries the same payload to the renderer for live inspection.
- Lead attribution (`parent_lead_id`) flows verbatim through the
  spawner closure — the routing layer picks the *provider*, not
  the *lead*. Sibling spawns with different tags can land on
  different providers; a worker that spawns a sub-worker
  re-consults the router so deeper nodes can route differently
  from the parent's choice.
- The `local_only` invariant is enforced twice: the resolver
  short-circuits cloud providers out for `local_only` projects,
  and the spawn site asserts the chosen provider's
  `capability_profile.local` flag before the run starts. A
  bypass raises `LocalOnlyViolation` and the spawn is reported
  `skipped` — the project's privacy invariant survives even when
  an upstream code path slipped past the routing layer.
- Conversational edits ("route coding to ollama in this project",
  "make this project local-only") land through a small intent
  parser in `thread.send`. Provider aliases collapse model-flavoured
  language to a registry key so the user isn't forced to think in
  provider-vs-model terms; misses fall through to the regular
  reply flow unchanged.
