"""Information-flow drift audit primitive.

ADR-0027 generalises the v1 worker drift primitive across the three
hops F12.7 names:

- ``plan_vs_action`` — a worker run's plan vs its action log
  (the existing v1 hop; `compute_drift_score` is its scoring fn).
- ``reported_vs_truth`` — a lead's report vs the underlying
  ground truth (action log when delegated work happened; the
  reply text + hedge / non-answer heuristics when it's a direct
  chat reply with no log to compare against).
- ``relayed_vs_source`` — the brain's relayed text vs the source
  reply it summarises. The brain → user hop runs on every relayed
  chat turn, so the heuristic layer carries the default path.

The primitive is intentionally a single entry point — callers pass a
``mode`` and the mode-specific source / output projection, and they
get back a uniform :class:`InfoFlowAuditReport`. The runtime renders
the report into a ``run.drift`` notification (with the new ``mode``
field) and into an ``info_flow_check`` action-log entry. When the
score and confidence indicate divergence, callers raise the
``info_flow`` approval gate (already in ``approvals.GATE_KINDS``).

Two layers ride together:

- A **heuristic** layer runs every call. It is fast (sub-millisecond
  for chat-sized payloads) and produces a coarse drift score plus a
  ``medium`` baseline confidence.
- An **LLM critic** layer escalates only when the heuristic crosses
  a threshold (``HEURISTIC_ESCALATION_THRESHOLD``) or the caller
  forces it (``force_llm=True``). The LLM verdict is ``max``-blended
  with the heuristic — the same combine shape ``combined_drift``
  established. Layer agreement raises confidence to ``high``; layer
  disagreement (one says drifted, the other says fine) drops it to
  ``low``.

The LLM call is opt-in via the ``provider`` argument. Callers in
hot paths (the brain → user relay) skip the provider and live with
the heuristic verdict by default; callers in deliberate paths (a
lead's wrap-up report) pass a provider so the audit can escalate.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal

from thalyn_brain.orchestration.drift import (
    _STOPWORDS,
    compute_drift_score,
)
from thalyn_brain.provider import ChatTextChunk, LlmProvider

InfoFlowConfidence = Literal["low", "medium", "high"]


class InfoFlowMode(StrEnum):
    """The three F12.7 audit modes the primitive multiplexes over."""

    PLAN_VS_ACTION = "plan_vs_action"
    REPORTED_VS_TRUTH = "reported_vs_truth"
    RELAYED_VS_SOURCE = "relayed_vs_source"


HEURISTIC_ESCALATION_THRESHOLD: float = 0.4
"""Heuristic score at or above which the LLM critic layer runs (if a
provider was supplied). Below this, the heuristic verdict is the only
signal; the audit is fast and the report's confidence stays at
``medium``."""

DEFAULT_GATE_THRESHOLD: float = 0.7
"""Drift score at and above which callers raise an ``info_flow`` gate.
Matches the existing worker-drift pause threshold so a future shared
settings dial covers both surfaces."""


@dataclass(frozen=True)
class InfoFlowAuditReport:
    """One audit verdict, mode-agnostic.

    ``source_ref`` and ``output_ref`` are provenance pointers the
    renderer's drill-into-source UX (F1.10) follows back to the
    underlying record. ``summary`` is the one-sentence reason the
    runtime renders inline.

    ``mode`` is the :class:`InfoFlowMode` value; ``confidence`` is
    the audit's own confidence in its verdict (not the source agent's
    confidence — that is reported on lead replies separately).
    """

    mode: InfoFlowMode
    drift_score: float
    confidence: InfoFlowConfidence
    summary: str
    source_ref: dict[str, Any] = field(default_factory=dict)
    output_ref: dict[str, Any] = field(default_factory=dict)
    heuristic_score: float = 0.0
    llm_score: float | None = None

    @property
    def should_raise_gate(self) -> bool:
        """``True`` when the audit warrants surfacing the user gate.

        High drift always raises. ``low`` confidence + medium drift
        also raises — layer disagreement is itself a signal worth
        flagging rather than silently passing through.
        """
        if self.drift_score >= DEFAULT_GATE_THRESHOLD:
            return True
        if self.confidence == "low" and self.drift_score >= HEURISTIC_ESCALATION_THRESHOLD:
            return True
        return False

    def to_wire(self) -> dict[str, Any]:
        """Camel-case wire shape for IPC notifications + log entries."""
        out: dict[str, Any] = {
            "mode": self.mode.value,
            "driftScore": self.drift_score,
            "confidence": self.confidence,
            "summary": self.summary,
            "sourceRef": dict(self.source_ref),
            "outputRef": dict(self.output_ref),
            "heuristicScore": self.heuristic_score,
        }
        if self.llm_score is not None:
            out["llmScore"] = self.llm_score
        return out


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def audit_info_flow(
    *,
    mode: InfoFlowMode,
    source: str | dict[str, Any] | None,
    output: str,
    provider: LlmProvider | None = None,
    context: dict[str, Any] | None = None,
    source_ref: dict[str, Any] | None = None,
    output_ref: dict[str, Any] | None = None,
    force_llm: bool = False,
) -> InfoFlowAuditReport:
    """Score ``output`` against ``source`` in the requested ``mode``.

    ``source`` shape depends on the mode:

    - ``plan_vs_action`` — ``source`` is a plan dict (``{"goal": ..,
      "nodes": [..]}``) and ``output`` is unused (kept ``""``).
      ``context['action_log']`` carries the action log. This path
      reuses :func:`compute_drift_score` so the v1 worker scoring is
      not duplicated.
    - ``reported_vs_truth`` — ``source`` is a structured ground-truth
      bundle (``{"action_log": [...], "facts": [...]}`` or ``None``
      for direct chat replies with no log). ``output`` is the lead's
      report text.
    - ``relayed_vs_source`` — ``source`` is the raw lead reply text;
      ``output`` is the brain's relay text (preamble stripped).

    Pass ``provider`` to enable the LLM-critic layer. The layer runs
    when the heuristic score is at or above
    :data:`HEURISTIC_ESCALATION_THRESHOLD`, or when ``force_llm`` is
    true (caller forced an escalation, e.g. a hard gate already
    landed and the audit needs to be defensible).
    """
    heuristic_score, summary = _heuristic_for(mode, source=source, output=output, context=context)

    llm_score: float | None = None
    llm_reason: str | None = None
    confidence: InfoFlowConfidence = "medium"

    should_escalate = force_llm or heuristic_score >= HEURISTIC_ESCALATION_THRESHOLD
    if provider is not None and should_escalate:
        llm_score, llm_reason = await _run_llm_critic(
            provider,
            mode=mode,
            source=source,
            output=output,
        )

    drift_score: float
    if llm_score is None:
        drift_score = heuristic_score
        # Heuristic alone is medium-confidence by default; a clearly
        # clean reading (well below escalation) is upgraded to high
        # so the renderer doesn't surface a pill for routine relays.
        if heuristic_score < (HEURISTIC_ESCALATION_THRESHOLD / 2):
            confidence = "high"
    else:
        drift_score = max(heuristic_score, llm_score)
        # Layer agreement (both either above or below escalation
        # threshold) → high. Disagreement → low. The blended score
        # is still max, so the runtime treats the worst signal as
        # truth even when confidence is low.
        heuristic_flagged = heuristic_score >= HEURISTIC_ESCALATION_THRESHOLD
        llm_flagged = llm_score >= HEURISTIC_ESCALATION_THRESHOLD
        if heuristic_flagged == llm_flagged:
            confidence = "high"
        else:
            confidence = "low"
        if llm_reason:
            summary = llm_reason

    return InfoFlowAuditReport(
        mode=mode,
        drift_score=max(0.0, min(1.0, drift_score)),
        confidence=confidence,
        summary=summary,
        source_ref=dict(source_ref or {}),
        output_ref=dict(output_ref or {}),
        heuristic_score=heuristic_score,
        llm_score=llm_score,
    )


# ---------------------------------------------------------------------------
# Mode-specific heuristic scoring
# ---------------------------------------------------------------------------


def _heuristic_for(
    mode: InfoFlowMode,
    *,
    source: str | dict[str, Any] | None,
    output: str,
    context: dict[str, Any] | None,
) -> tuple[float, str]:
    match mode:
        case InfoFlowMode.PLAN_VS_ACTION:
            return _heuristic_plan_vs_action(source=source, context=context)
        case InfoFlowMode.REPORTED_VS_TRUTH:
            return _heuristic_reported_vs_truth(source=source, output=output)
        case InfoFlowMode.RELAYED_VS_SOURCE:
            return _heuristic_relayed_vs_source(source=source, output=output)


def _heuristic_plan_vs_action(
    *,
    source: str | dict[str, Any] | None,
    context: dict[str, Any] | None,
) -> tuple[float, str]:
    plan = source if isinstance(source, dict) else None
    action_log: list[dict[str, Any]] = []
    if context is not None:
        log_value = context.get("action_log")
        if isinstance(log_value, list):
            action_log = log_value
    score = compute_drift_score(plan, action_log)
    if score == 0.0:
        return 0.0, "action log covers every plan node"
    nodes = (plan or {}).get("nodes") or []
    matched = round((1.0 - score) * len(nodes))
    return score, f"action log matches {matched}/{len(nodes)} plan nodes"


_HEDGE_PHRASES: tuple[str, ...] = (
    "i don't know",
    "i do not know",
    "i'm not sure",
    "i am not sure",
    "no idea",
    "unclear",
)


def _heuristic_reported_vs_truth(
    *,
    source: str | dict[str, Any] | None,
    output: str,
) -> tuple[float, str]:
    stripped = output.strip()
    if not stripped:
        return 1.0, "report is empty"
    lowered = stripped.lower()
    for hedge in _HEDGE_PHRASES:
        if lowered.startswith(hedge):
            # Leading hedge is the F1.8 "I'm not sure" surface — the
            # report itself is honest about its uncertainty. Score is
            # mid-band: the runtime should flag low-confidence, not
            # block the relay.
            return 0.55, "report opens with a hedge phrase"

    # When the caller supplied a structured ground truth, cross-check
    # that the numbers / quoted identifiers in the report appear in
    # the source. A claim that doesn't trace back to the underlying
    # action log is the classic "lead made it up" failure mode.
    if isinstance(source, dict):
        return _check_claims_against_source(output=output, source=source)

    # No structured ground truth and no opening hedge — the heuristic
    # has nothing to say. Pass through at a low score; the LLM critic
    # (when escalated) is the deeper check.
    return 0.0, "report passes lightweight heuristic checks"


def _check_claims_against_source(
    *,
    output: str,
    source: dict[str, Any],
) -> tuple[float, str]:
    log_value = source.get("action_log")
    action_log: list[dict[str, Any]] = log_value if isinstance(log_value, list) else []
    facts_value = source.get("facts")
    facts: list[str] = [str(f) for f in facts_value] if isinstance(facts_value, list) else []

    haystack_parts: list[str] = []
    for entry in action_log:
        haystack_parts.extend(_strings_in(entry))
    haystack_parts.extend(facts)
    haystack = " ".join(haystack_parts).lower()

    claim_tokens = _extract_claims(output)
    if not claim_tokens:
        return 0.0, "report carries no falsifiable claims"

    missing = [tok for tok in claim_tokens if tok.lower() not in haystack]
    if not missing:
        return 0.0, "every reported claim appears in the underlying source"

    miss_fraction = len(missing) / len(claim_tokens)
    sample = ", ".join(missing[:3])
    return (
        miss_fraction,
        f"{len(missing)}/{len(claim_tokens)} reported claims missing from source ({sample})",
    )


_NUMBER_RE = re.compile(r"\b\d[\d.,]*\b")
_QUOTED_RE = re.compile(r"`([^`\n]{2,80})`|\"([^\"\n]{2,80})\"")


def _extract_claims(text: str) -> list[str]:
    """Numbers and code-spanned / quoted identifiers — the falsifiable
    pieces of a report. Free prose isn't checked; the LLM critic is
    where prose-level audits happen."""
    out: list[str] = []
    for match in _NUMBER_RE.finditer(text):
        out.append(match.group(0))
    for match in _QUOTED_RE.finditer(text):
        out.append(match.group(1) or match.group(2))
    # Dedup, preserve order
    seen: set[str] = set()
    deduped: list[str] = []
    for item in out:
        if item.lower() in seen:
            continue
        seen.add(item.lower())
        deduped.append(item)
    return deduped


def _heuristic_relayed_vs_source(
    *,
    source: str | dict[str, Any] | None,
    output: str,
) -> tuple[float, str]:
    source_text = source if isinstance(source, str) else ""
    if not source_text.strip():
        # The source is empty — nothing to relay drift away from.
        if output.strip():
            return 0.0, "no source text to compare relay against"
        return 0.0, "relay and source are both empty"

    confidence_collapse = _detects_confidence_collapse(source_text, output)
    overlap = _shingle_overlap(source_text, output)
    # Overlap is the share of source content tokens that survived the
    # relay. Invert: drift is 1 - overlap, weighted toward the upper
    # half so a relay that drops most of the source reads as drifted.
    overlap_drift = max(0.0, 1.0 - overlap)
    # The confidence-collapse signal is a hard bump regardless of
    # overlap — the relay dropped the source's hedging, which is the
    # exact failure F12.8 names.
    drift = overlap_drift
    summary: str
    if confidence_collapse:
        drift = max(drift, 0.6)
        summary = "relay drops the hedging the source carried"
    elif overlap_drift > 0.5:
        summary = f"relay shares {overlap * 100:.0f}% of source content tokens"
    else:
        summary = "relay preserves source content"
    return drift, summary


def _detects_confidence_collapse(source: str, output: str) -> bool:
    source_lower = source.lower()
    output_lower = output.lower()
    source_hedged = any(h in source_lower for h in _HEDGE_PHRASES) or source_lower.count("?") >= 2
    output_hedged = any(h in output_lower for h in _HEDGE_PHRASES) or output_lower.count("?") >= 2
    return source_hedged and not output_hedged


_SHINGLE_TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9_]+")


def _shingle_overlap(source: str, output: str) -> float:
    """Content-word overlap between source and output as a fraction
    of source content words. Drops stopwords (drift.py's set) so
    the score reflects subject-matter overlap rather than function
    words a paraphrase keeps for free.
    """
    source_words = _content_words(source)
    output_words = set(_content_words(output))
    if not source_words:
        return 1.0
    matched = sum(1 for word in source_words if word in output_words)
    return matched / len(source_words)


def _content_words(text: str) -> list[str]:
    return [
        token.lower()
        for token in _SHINGLE_TOKEN_RE.findall(text)
        if len(token) > 2 and token.lower() not in _STOPWORDS
    ]


def _strings_in(value: Any) -> list[str]:
    out: list[str] = []
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, bool):
        # ``bool`` is a subclass of ``int``; check it first so True/False
        # don't end up in the claim haystack as the strings "1"/"0".
        pass
    elif isinstance(value, int | float):
        # Coerce numeric payload fields so a claim like "71 tests" can
        # match an action-log entry that recorded ``tests_run: 71``.
        out.append(repr(value))
    elif isinstance(value, dict):
        for nested in value.values():
            out.extend(_strings_in(nested))
    elif isinstance(value, list):
        for nested in value:
            out.extend(_strings_in(nested))
    return out


# ---------------------------------------------------------------------------
# LLM critic escalation
# ---------------------------------------------------------------------------


_INFO_FLOW_CRITIC_SYSTEM_PROMPT = """You are the information-flow critic inside a brain runtime.

Given a source artefact and a derived output, judge whether the output
faithfully represents the source. Respond with a single JSON object
matching this exact shape and nothing else:

{
  "drift_score": <number between 0.0 and 1.0>,
  "reason": "<one or two sentences explaining the score>"
}

A drift_score near 0 means the output accurately reflects the source;
near 1 means the output diverges meaningfully (omits a load-bearing
hedge, invents a claim not present in the source, contradicts a
specific number). Do not include any prose outside the JSON object.
"""


_MODE_FRAMINGS: dict[InfoFlowMode, str] = {
    InfoFlowMode.PLAN_VS_ACTION: (
        "The source is the agent's approved plan; the output is the cumulative action log. "
        "Drift is the share of plan steps the agent has not yet pursued (or has detoured from)."
    ),
    InfoFlowMode.REPORTED_VS_TRUTH: (
        "The source is the lead's underlying state (action log, project facts). The output is "
        "the lead's written report. Drift is unsubstantiated claims, missing context the user "
        "needs, or smoothing-over of uncertainty."
    ),
    InfoFlowMode.RELAYED_VS_SOURCE: (
        "The source is what the lead actually said. The output is the brain's relayed text to "
        "the user. Drift is paraphrase that loses content, drops hedging, or invents claims."
    ),
}


async def _run_llm_critic(
    provider: LlmProvider,
    *,
    mode: InfoFlowMode,
    source: str | dict[str, Any] | None,
    output: str,
) -> tuple[float | None, str | None]:
    prompt = _build_llm_prompt(mode=mode, source=source, output=output)
    parts: list[str] = []
    async for chunk in provider.stream_chat(prompt, system_prompt=_INFO_FLOW_CRITIC_SYSTEM_PROMPT):
        if isinstance(chunk, ChatTextChunk):
            parts.append(chunk.delta)
    return _parse_llm_response("".join(parts))


def _build_llm_prompt(
    *,
    mode: InfoFlowMode,
    source: str | dict[str, Any] | None,
    output: str,
) -> str:
    source_text: str
    if isinstance(source, str):
        source_text = source
    elif isinstance(source, dict):
        try:
            source_text = json.dumps(source, indent=2, sort_keys=True)
        except (TypeError, ValueError):
            source_text = str(source)
    else:
        source_text = "(no structured source)"
    framing = _MODE_FRAMINGS[mode]
    return (
        f"Mode: {mode.value}\n"
        f"Framing: {framing}\n\n"
        f"Source:\n{source_text}\n\n"
        f"Output:\n{output}\n\n"
        "Respond with the JSON object the system prompt specifies."
    )


_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_llm_response(text: str) -> tuple[float | None, str | None]:
    match = _JSON_OBJECT_RE.search(text)
    if match is None:
        return None, None
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None, None
    if not isinstance(payload, dict):
        return None, None
    score_raw = payload.get("drift_score")
    reason_raw = payload.get("reason")
    score: float | None = None
    if isinstance(score_raw, int | float):
        score = max(0.0, min(1.0, float(score_raw)))
    reason = reason_raw if isinstance(reason_raw, str) else None
    return score, reason


def info_flow_check_log_entry(report: InfoFlowAuditReport) -> dict[str, Any]:
    """Build an ``info_flow_check`` action-log entry payload from a
    report. The runtime appends this on every audit run — flagged or
    not — so a future hash-chained audit (going-public-checklist) has
    the full record to replay.
    """
    return {
        "kind": "info_flow_check",
        "payload": report.to_wire(),
    }


def confidence_level_for_drift(drift_score: float) -> InfoFlowConfidence:
    """Translate a drift score into a user-facing confidence level.

    High drift → low confidence in the underlying claim. This is the
    F12.8 surface — the renderer reads the level off the lead turn's
    ``confidence`` field to render a pill. The cutoffs are chosen so
    routine audits sit at ``high`` (no pill), flagged-but-not-gated
    audits sit at ``medium`` (subtle pill), and gate-worthy audits
    sit at ``low`` (prominent pill + gate card).
    """
    if drift_score < 0.3:
        return "high"
    if drift_score < DEFAULT_GATE_THRESHOLD:
        return "medium"
    return "low"


def report_to_confidence_payload(report: InfoFlowAuditReport) -> dict[str, Any]:
    """Build the ``confidence`` wire payload that lands on a
    ``THREAD_TURN`` row.

    The renderer's chat surface reads ``confidence.level`` to render
    the low-confidence pill, ``confidence.audit`` to render the
    drill-into-source link, and ``confidence.audit.summary`` for the
    pill's tooltip.
    """
    return {
        "level": confidence_level_for_drift(report.drift_score),
        "audit": report.to_wire(),
        "audits": [report.to_wire()],
    }


_LEVEL_RANK: dict[InfoFlowConfidence, int] = {"high": 0, "medium": 1, "low": 2}


def combine_confidence_payloads(*reports: InfoFlowAuditReport) -> dict[str, Any]:
    """Build a combined confidence payload from one or more audits.

    The combined ``level`` is the worst across the inputs (a single
    low-confidence audit pulls the combined level to low).
    ``audit`` keeps the worst report as the primary surface for the
    renderer's pill + drill-down; ``audits`` carries every audit so
    the user can navigate to each underlying source.
    """
    if not reports:
        raise ValueError("at least one audit report is required")
    primary = max(reports, key=lambda r: _LEVEL_RANK[confidence_level_for_drift(r.drift_score)])
    return {
        "level": confidence_level_for_drift(primary.drift_score),
        "audit": primary.to_wire(),
        "audits": [r.to_wire() for r in reports],
    }
