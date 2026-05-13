# ADR-0027 — Information-flow drift: critic generalization across the EM hierarchy

- **Status:** Proposed
- **Date:** 2026-05-12
- **Deciders:** Barlow
- **Supersedes:** —
- **Superseded by:** —

## Context

The v1 drift primitive (carried into v2 as `orchestration/drift.py` +
`orchestration/critic.py`) audits one hop only: **a worker run's plan
versus its action log**. It compares plan nodes against the cumulative
tool-call / decision stream, blends an LLM critic verdict with a
keyword-overlap heuristic, and fires a `gateKind="drift"` approval when
either signal flags wandering. That hop is the worker's
*internal* coherence — "is this run still doing what it was approved
to do?"

The v2 management hierarchy (ADR-0021) introduces two further hops
where information can warp before it reaches the user. F12.7 names
them explicitly:

1. **Lead → brain.** A lead reports outcomes upward. The report is a
   summary of what the lead and its workers actually did, plus a
   verdict ("done", "blocked on X", "needs your call on Y"). A
   plausible-sounding report that doesn't match the action log is
   indistinguishable from a true report at the brain layer unless
   something audits it.
2. **Brain → user.** Thalyn relays the lead's reply with a preamble
   ("Asking Lead-X now… Lead-X says: …"). The relayed text can drift
   from the source — paraphrasing that omits a hedge, an editorial
   "everything looks fine" that the lead never said, a forwarded
   number that flipped a sign. The relay itself is the audit
   boundary; the user has no other surface against which to check
   the brain.

The v1 lead → brain seat already exists as `sanity_check_lead_reply`
in `lead_delegation.py`, but its scope is narrow: empty bodies and a
short list of leading hedge phrases. It satisfies F1.8's call-site
("each tier sanity-checks the tier below before passing up") but
doesn't generalise the *drift primitive* itself. The brain → user
hop has no audit seat at all today.

F12.7 calls these "information-flow drift" and frames them as the
same primitive as the worker plan-vs-action drift. F1.8 frames them
as accuracy invariants: confidence is a first-class value and
uncertainty surfaces rather than smooths over. F1.10 frames the
remediation: every relayed claim is one click from its source.
v0.37's job is to fold all three into one shared abstraction.

Three forces shape the design:

- **One primitive, three call sites.** The audit shape — *given an
  output and the source it claims to summarise, score how far they
  diverge* — is the same whether the output is a worker's action log
  rolled up against its plan, a lead's report rolled up against its
  conversation/action context, or the brain's relayed text rolled up
  against the lead's raw reply. Building three independent scorers
  would mean three drift dialects, three tuning passes, three sets
  of edge-case bugs. Build it once.
- **Critic latency is the load-bearing constraint at the brain → user
  hop.** A worker run's drift audit fires at budget checkpoints
  (25 / 50 / 75 % of tokens/time). The lead → brain hop runs once per
  reply — still cheap. The brain → user hop runs *on every relayed
  chat turn the user sees* — the conversational latency budget
  (NFR1) is the constraint. An LLM round-trip per relay would
  degrade chat to a halt. The heuristic layer has to carry the
  default path; the LLM critic is opt-in for high-stakes relays
  (large summaries, claim-dense reports, runs already flagged
  by the lead's own confidence).
- **Confidence is first-class, not a euphemism.** F12.8 says reports
  carry confidence flags (`low | medium | high`). The audit emits a
  confidence value alongside its drift score; the renderer chooses
  the surface (subtle pill at `medium`, prominent warning + gate at
  `low`). The "I'm not sure" path is the *correct* lead behaviour,
  not a regression to apologise for.

The information-flow audit logs are a new audit-log shape — they
record the comparison itself, not just the verdict — so a future
hash-chained audit (going-public-checklist) can replay them. v1
keeps them as ordinary NDJSON entries; the chaining lands at the
public-release pass.

## Decision

Adopt a **single mode-parameterised audit primitive** that all three
F12.7 hops share, layered like the existing worker drift: a fast
heuristic that runs always, plus an LLM critic that the runtime
escalates to under named conditions.

### 1. The shared primitive

```python
class InfoFlowMode(StrEnum):
    PLAN_VS_ACTION    = "plan_vs_action"     # worker: plan ↔ action log
    REPORTED_VS_TRUTH = "reported_vs_truth"  # lead ↔ brain
    RELAYED_VS_SOURCE = "relayed_vs_source"  # brain ↔ user

@dataclass(frozen=True)
class InfoFlowAuditReport:
    mode: InfoFlowMode
    drift_score: float          # 0.0 (aligned) .. 1.0 (divergent)
    confidence: Literal["low", "medium", "high"]
    summary: str                # one-sentence reason
    source_ref: dict[str, Any]  # provenance pointer to the source
    output_ref: dict[str, Any]  # provenance pointer to the output

async def audit_info_flow(
    *,
    mode: InfoFlowMode,
    source: str | dict[str, Any],
    output: str,
    provider: LlmProvider | None = None,
    context: dict[str, Any] | None = None,
) -> InfoFlowAuditReport: ...
```

The function is the *only* public entry point. Mode-specific helpers
inside it choose the source and output projection (plan-tree text for
plan_vs_action, reply text + question-density for reported_vs_truth,
relay text + source text for relayed_vs_source), but the audit core
— score, confidence, summary — is shared.

### 2. The two layers

- **Heuristic layer (default).** Always runs. Cheap.
  - `plan_vs_action`: the existing `compute_drift_score` (plan-node
    keyword coverage in the action log).
  - `reported_vs_truth`: the lead-reply hedge / non-answer scanner
    from v0.23, generalised to also flag claim density mismatch
    (the lead claims completion but the underlying action log is
    empty; the lead claims a number that doesn't appear in any
    underlying tool result).
  - `relayed_vs_source`: token-overlap between the relay's
    non-template content and the source reply, normalized for length;
    a flag for the relay containing a confidence-collapse (the lead
    hedged but the relay didn't) is a hard signal regardless of
    overlap.
- **LLM-critic layer (opt-in escalation).** The runtime calls the
  critic when:
  - heuristic score ≥ a per-mode threshold (default 0.4); or
  - the underlying agent already self-flagged low-confidence; or
  - the call site explicitly requests it (e.g. a hard gate landed,
    or the report exceeds a size threshold).
  The critic prompt is mode-tagged so the model sees the right
  framing; the parsed verdict is `max`-blended with the heuristic
  (same pattern as `combined_drift`).

The blended score (heuristic, optionally combined with the LLM
verdict) is what the runtime acts on. The confidence value reports
how much agreement existed between the layers — heuristic alone
yields `medium`; heuristic + LLM agreement yields `high`; heuristic
+ LLM disagreement yields `low`.

### 3. The wire surface

- The existing `run.drift` notification gains a `mode` field
  (`02-architecture.md` §6.2 already names the three values). Lead
  and brain hops emit it the same way worker runs do.
- The existing `info_flow` `gateKind` (already in
  `02-architecture.md` §5 and `approvals.py`'s `GATE_KINDS` set)
  is the approval kind the renderer surfaces when a `reported_vs_truth`
  or `relayed_vs_source` audit flags `high` drift. Resolving the
  gate gives the user the standard *approve / reject / request
  clarification* options. The `infoFlowSummary?` payload on
  `run.approval_required` (already named) carries the audit's
  summary + source/output refs so the gate card can render
  drill-into-source links inline.
- The action log gains an `info_flow_check` kind (already named in
  the architecture doc §6.2 entry kinds list). Every audit run
  appends one entry whether it flagged or not — the audit *fact* is
  part of the run's record, not only the *flagging*. The payload
  carries the full `InfoFlowAuditReport.to_wire()` so a future
  drill-down can render the audit without re-running it.

### 4. The brain → user latency path

The relayed_vs_source audit runs *synchronously* on the heuristic
layer (sub-millisecond) before the brain's outgoing turn is
finalised; the LLM critic only escalates when the heuristic threshold
is crossed. If the LLM critic does escalate and the verdict raises
the gate, the brain holds the outgoing turn at a *holdable boundary*
— the relayed text is delivered to the renderer but flagged with a
prominent warning + gate card; the underlying lead row is already in
the eternal thread so the user can drill into the source even while
the gate is pending. The relay isn't suppressed because the user's
ability to read the source is itself the audit; we surface
disagreement, we don't censor.

### 5. Confidence flags on lead reports

The lead's reply object grows a `confidence: "low" | "medium" |
"high"` field. Today's `SanityCheckVerdict.ok` (binary) maps to
`high` (true) or `low` (false); future LLM-judge layers can refine
the middle. The renderer surfaces a confidence pill on the lead's
attribution chip — visually subtle at `medium`, prominent at `low`.
The pill is link-targeted to the same provenance ref the relay's
audit refers to, so a user click drills into the source.

## Consequences

- **Positive.**
  - One primitive to debug, tune, and instrument. Three call sites
    share their drift dialect.
  - The heuristic-first design keeps relay latency within NFR1 chat
    budgets. The LLM escalation gives high-stakes claims the deeper
    audit they need without paying for it on every turn.
  - F1.8's "each tier sanity-checks the tier below" cashes out as
    actual code at every tier instead of a posture statement that
    only the worker hop honours today.
  - The audit log's `info_flow_check` entries give a future
    hash-chained audit something to chain over.
- **Negative.**
  - The heuristic layer will have false positives — a lead's
    legitimate paraphrase will sometimes score low overlap with the
    raw reply. Default behaviour is to surface a `medium`-confidence
    pill, not a gate; only `low` confidence + high heuristic score
    raises the gate. The tuning will need to evolve as users tell us
    which signals are noise.
  - The LLM critic adds a per-call cost when it escalates. The
    escalation thresholds are user-tunable (settings UI lands in the
    polish phase) so noisy users can raise them.
- **Neutral.**
  - The wire shapes were named ahead in the architecture doc
    (`run.drift.mode`, `info_flow` gate, `info_flow_check` log
    entry, `infoFlowSummary?` on approval payload), so v0.37 fills
    them in rather than reshaping the IPC contract.

## Alternatives considered

- **Three independent audit functions, one per hop.** Cleaner local
  call sites, but three separately-tuned scoring algorithms with
  three sets of edge-case quirks. Rejected on the "one primitive"
  argument above. The mode parameter is a cheap cost for a unified
  scoring model.
- **LLM critic always, no heuristic.** Most accurate but blows the
  NFR1 latency budget at the brain → user hop. Rejected; chat has
  to feel like chat.
- **Heuristic only, no LLM escalation.** Cheap and fast, but the
  heuristic's false-negative rate on subtle paraphrase drift is
  exactly the failure mode F1.8 exists to defend against. Rejected;
  the escalation path is the load-bearing claim of the design.
- **Run the audit asynchronously and emit a correction notification.**
  Considered for high-throughput cases. Deferred — the user-facing
  surface should not race the relay it audits in v1. v1.x may
  revisit when async-correction UX patterns mature.

## References

- `01-requirements.md` §F1.8, §F12.7, §F12.8, §F1.10.
- `02-architecture.md` §4.2 (drift monitor + critic generalized),
  §5 (`APPROVAL.gate_kind` enum), §6.2 (notifications and entry
  kinds), §10.2 (info-flow drift in the v2-specific defences),
  §11 (`thalyn.info_flow_audit_score` span attribute), §13 risks.
- ADR-0021 — Agent hierarchy. The three-hop topology this ADR
  audits.
- ADR-0009 — Memory. Refined in this phase to carry provenance
  fields on `MEMORY_ENTRY` so the audit can chain across recall.
- `brain/thalyn_brain/orchestration/critic.py` and
  `orchestration/drift.py` — the existing v1 worker-drift primitive
  this ADR generalises.
- `brain/thalyn_brain/lead_delegation.py::sanity_check_lead_reply`
  — the existing lead → brain seat this ADR extends.
