# ADR-0024 — Project mobility (merge) + pluggable classifier interface

- **Status:** Accepted
- **Date:** 2026-05-11 (Proposed) · 2026-05-11 (Accepted)
- **Deciders:** Barlow
- **Supersedes:** —
- **Superseded by:** —

## Context

Two complementary capabilities land together in v0.35: **project merge**
(F3.4) and the **pluggable classifier interface** (F3.5 / F11.3). Both
exist for the same reason — project boundaries are not fixed at
creation, and the user should not pay for picking them wrong.

The classifier decides whether an inbound message belongs to an
existing project. The conservative default is `suggest`, not `auto`:
the classifier proposes, the user (or the renderer's confirm dialog)
accepts. That default is only tenable if a misplaced project is cheap
to reshape after the fact. Merge is the reshape primitive — `move A
into B` is `merge A into B + archive A`. Without merge, every
classifier mistake is permanent and the conservative default becomes
oppressive: users learn to refuse all suggestions, and project sprawl
ramps the other way.

Three forces shape the design:

- **Correctness under concurrent activity.** A merge mutates a lot of
  state in one shot — thread tags, memory rows, the absorbed lead's
  record, routing overrides, connector grants, an audit entry. A
  worker run mid-flight on the absorbed project must not see a torn
  half-merge. Spec risk #8 (`02-architecture.md` §13) flags this
  explicitly: the merge needs a transaction model.
- **Reversibility, where it's cheap.** v1 doesn't ship undo-merge —
  it's on the v1.x list. But every merge writes an immutable audit
  entry capturing the full plan and outcome, so a future
  reverse-merge tool has the data it needs. Audit-as-recovery-surface,
  not audit-as-after-the-fact-log.
- **Pluggability without a parallel substrate.** The classifier is one
  of the F11.3 plugin object kinds. v1 ships exactly one registered
  implementation (the LLM-judge default), but the interface has to be
  the real interface — same shape v1.x will register user-supplied
  declarative classifiers against. A vestigial Protocol the v1 code
  doesn't actually exercise wouldn't catch the regressions that
  matter.

## Decision

Two coupled mechanisms, each with a narrow surface.

### 1. Project merge

A two-phase shape: **plan first, apply on renderer confirmation.**

- **`compute_merge_plan(from_project, into_project, *, stores) ->
  MergePlan`** — a pure read against the stores that produces a
  `MergePlan` dataclass. The plan captures every row that would be
  rewritten (turn ids, memory entry ids, the absorbed lead's id, any
  sub-leads to re-parent, routing-override conflicts, connector-grant
  conflicts). The plan is JSON-serialisable so the renderer can
  display the consequence sheet before the user confirms.
- **`apply_merge_plan(plan, *, stores, audit) -> MergeOutcome`** — a
  single SQLite transaction (`BEGIN IMMEDIATE` on the shared
  `app.db`) that executes the plan. The transaction wraps every write
  — thread-turn re-tagging, memory-row migration, lead retirement,
  routing-override merge, project status flip — so a crash mid-merge
  rolls everything back. An NDJSON audit entry under
  `data_dir/merges/<merge_id>.log` captures the plan + outcome before
  the transaction commits.
- **`project.merge` IPC method** — `{fromProjectId, intoProjectId,
  apply: bool}`. With `apply: false` (the default) the brain returns
  the plan only. With `apply: true` the brain applies the plan and
  returns `{plan, outcome, mergeId}`. The renderer is responsible for
  re-fetching the plan before confirming so the user sees the live
  state, not a stale dry-run.

Merge semantics per F3.4:

- **Thread turns.** `thread_turns.project_id` rewrites from the
  absorbed project to the surviving project. The eternal-thread
  search (FTS5) reads from `thread_turn_index`, which auto-mirrors
  inside the same transaction.
- **Memory entries.** `memory_entries.project_id` migrates from
  absorbed to surviving. The absorbed lead's authored rows (where
  `agent_id` references the absorbed lead) re-anchor onto the
  surviving lead so the surviving lead's namespace stays internally
  consistent — "what does the lead remember about its project?"
  reads cleanly. The rows themselves are not deleted; provenance
  shifts but content survives.
- **Agent records.** The absorbed lead transitions to `archived`
  via `LeadLifecycle.archive`; its `agent_records.project_id` blanks
  out so `project_archive`'s usual cascade is mirrored. Sub-leads
  (parent_agent_id = absorbed lead id, when v0.36 lands) re-parent
  onto the surviving lead in the same transaction. v0.35 handles
  the no-sub-leads case; v0.36 extends `compute_merge_plan` to
  surface the re-parent list and `apply_merge_plan` walks it.
- **Routing overrides.** Rows on the absorbed project move to the
  surviving project. On `(task_tag)` collision, the surviving
  project's row wins; the absorbed override is dropped and the
  conflict is recorded in the audit entry. The user can re-apply
  the absorbed override post-merge if they wanted that one.
- **Connector grants.** Stored as a JSON blob on each project row.
  The merge takes the union: keys present in either project end up
  in the surviving project. On value conflict (both projects grant
  the same connector but with different scope), the surviving
  project's value wins; the conflict is in the audit entry.
- **Project status.** The absorbed project flips to `archived`. Its
  slug, conversation tag, name, and metadata are preserved so
  episodic search can still surface the old tag — searches read
  rows by their now-rewritten `project_id`, but the original tag
  string lives in `thread_turns.body`.
- **Audit entry.** One NDJSON file per merge under
  `data_dir/merges/<merge_id>.log`. First line is the full plan;
  second line is the outcome (rows actually written, conflicts
  resolved). Append-only, no signing in v1 (mirrors the
  per-run audit log conventions per ADR-0027's eventual hardening
  pass).

The `project.merge` action also appears in the F9.4 action registry
so "Thalyn, merge Project A into Project B" hits the same code path
as the IPC method.

### 2. Pluggable classifier interface

The `Classifier` Protocol from v0.31 is the v1 interface, unchanged
in shape. v0.35 generalises the single classifier slot into an
ordered composite:

- **`CompositeClassifier`** — wraps an ordered tuple of classifiers.
  On each call it asks every classifier for a verdict, then resolves
  by priority: **deterministic classifiers beat the LLM judge on
  ties.** The Protocol gains a `priority` attribute (an integer; lower
  values fire first), and the composite picks the highest-priority
  *confident* verdict over a less-confident one regardless of order.
- **v1 default registration.** The brain registers
  `LlmJudgeClassifier` at priority `100`. v1.x's declarative
  user-supplied classifier loader will register at priority `10` (or
  lower), so deterministic rules naturally outrank the LLM. No new
  config surface ships in v1; the composite is wired in
  `__main__.py` with a single classifier.
- **Verdict extension for "suggest, don't create."** `ClassifierVerdict`
  gains an optional `suggest_new_project: NewProjectSuggestion | None`
  field — name + slug + rationale. The default classifier's prompt is
  extended so the LLM can either return a known `projectId` or a
  proposed new-project shape when none of the candidates fit. The
  verdict's `decision` discriminator stays implicit (project_id =
  route_to_existing; suggest_new_project = suggest; both null =
  one_off / leave untagged).
- **`project.classify` returns the suggestion** so the renderer can
  surface "Should I create a new project named X?" without
  re-running the classifier.
- **`thread.send` surfaces the suggestion** in its response payload
  (`projectSuggestion`) when the classifier proposes a new project
  and the foreground bias didn't take. The renderer prompts the
  user; on confirmation the existing `project.create` action handles
  the create. v1 default is suggest, not auto — no project ever
  comes into existence without the user agreeing.

## Consequences

- **Positive.**
  - **Merge correctness is one transaction.** `BEGIN IMMEDIATE` plus
    foreign-key cascades means a crash mid-merge leaves the stores
    in a consistent pre-merge state. The audit entry is the only
    artefact a crash before commit can leave behind, and it self-
    identifies as incomplete (the outcome line is absent).
  - **Plan-first removes the worst foot-guns.** The renderer shows the
    user exactly what will change — conversation-tag count, memory-row
    count, sub-leads to re-parent, conflicts — before they confirm.
    A surprise merge is structurally hard to author.
  - **Classifier interface proves itself in v1.** The composite shape
    means v1.x's user-supplied classifiers slot in without touching
    `thread.send` or `project.classify`. The fake `RegexClassifier`
    test in v0.35 exercises the composite to prove the interface is
    real, not vestigial.
  - **Suggest mode is the safe default.** Project sprawl cannot
    happen without an explicit confirm; the LLM judge can be
    aggressive about suggesting new projects without that
    aggressiveness mutating state.
- **Negative.**
  - **Merge is not reversible in v1.** Undo-merge is v1.x; the audit
    entry is the data the future tool reads from. Users who merge
    in error and want to un-do it have to recreate the absorbed
    project manually — a documented limitation.
  - **Connector-grant conflict resolution is opinionated.** "Surviving
    project wins" is the rule even when the absorbed project granted
    a wider scope; the user has to re-grant manually if that's not
    what they wanted. The audit entry surfaces the conflict so it's
    discoverable; v1.x can add a per-conflict picker if real usage
    shows it's needed.
  - **The classifier prompt grows.** The new-project-suggestion branch
    adds ~200 tokens to every classifier call. For a busy day with
    20 untagged turns that's ~4K extra tokens — well under the
    budget, but not free.
- **Neutral.**
  - **Audit log is NDJSON, not SQL.** Same shape as the per-run audit
    log under `data_dir/runs/`. Easier to grep, harder to query;
    consistent with the existing pattern and a deliberate choice
    given the v1 hardening posture.
  - **Sub-lead re-parenting is deferred.** v0.36 adds the
    `re_parent_sub_leads` field to `MergePlan`. v0.35 leaves the
    field present but always empty — the test that re-parents
    sub-leads is the v0.36 phase's deliverable.

## Alternatives considered

- **One-shot `project.merge` with no plan phase.** Rejected. The
  consequences of merge are surface area the user must see before
  agreeing — count of memory rows, conversation-tag impact, conflicts.
  A one-shot IPC method makes the renderer reconstruct the plan
  client-side, which couples the renderer to the merge implementation
  and turns "what does this do" into a question with two answers (the
  brain's and the renderer's).
- **Merge as a renderer-side script that calls the existing CRUD
  endpoints.** Rejected. Merge is a multi-table mutation that must be
  transactional; reading from the renderer would mean opening N
  cross-table reads under N round-trips and applying them via N more
  writes. The atomicity invariant requires one server-side transaction.
- **Synchronous merge that blocks until done.** Considered; this is
  what `project.merge` actually does. The merge is fast (tens of ms
  for v1-sized data) — the alternative (kick off async, poll for
  status) adds plumbing without buying anything until merges grow
  large enough that a request times out. Revisit if real-world data
  pushes merges past the 5-second RPC budget; not v1 scope.
- **`Classifier` as a single registered implementation, no
  composite.** Rejected by the spec — F3.5 promises user-supplied
  classifiers in v1.x, and the F11.3 plugin contract treats
  classifiers as one of the plug-in object kinds. A composite is the
  v1 placeholder that makes v1.x additive instead of a retrofit.
- **Auto-create projects when the classifier is confident enough.**
  Tempting but rejected: the user's expressed preference (and the
  spec's default) is `suggest`, not `auto`. The cost of over-suggesting
  is a click; the cost of over-creating is a clean-up merge. Auto
  mode is a config option v1.x can light up if a user explicitly
  asks for it; not the v1 default.
- **Reverse-merge / undo as a v1 feature.** Rejected for v1 scope.
  Real merges land mostly correctly because the plan phase forces
  scrutiny. Add undo when there's enough real usage to know what
  "undo" should and shouldn't restore (e.g., the memory rows the user
  wrote *after* the merge — should they go back to the absorbed
  namespace?). v1.x is the right time.

## References

- `01-requirements.md` §F3.4 (project mobility), §F3.5 (pluggable
  classifier), §F11.3 (plugin object kinds).
- `02-architecture.md` §6 (`project.merge` / `project.classify` IPC
  shapes), §13 risk #8 (merge correctness under concurrent activity).
- ADR-0021 (agent hierarchy — the absorbed lead retires through the
  same lifecycle the project surface uses).
- ADR-0023 (worker model routing — the routing-override table whose
  rows merge re-parents).
- `project_project_mobility`, `project_classifier_scaffolding`
  memories (cross-conversation context).
