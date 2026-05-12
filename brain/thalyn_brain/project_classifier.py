"""Project classifier — pluggable interface plus the v1 default.

Per F3.5 the classifier interface ships with v1 and one default
implementation (LLM-judges, prompt-based). v1.x will register
user-supplied declarative classifiers without changing the interface.

The classifier's job inside the eternal-thread flow: when the user
sends a turn without an explicit ``projectId`` (no foreground
attention from the renderer, no ``@Lead-X`` mention), look at the
message and the active projects and decide which project this turn
belongs to. It returns ``None`` when no candidate is a confident
match — the caller then leaves the turn untagged rather than
forcing an arbitrary route. Confidence thresholds are configurable
so a future LLM-judge variant can plug in without changing the call
site.

The default implementation (``LlmJudgeClassifier``) prompts the
brain provider with the candidate set and asks for a JSON verdict.
A flaky model never breaks the eternal-thread tier — parse errors
collapse to ``None`` and the turn stays untagged, mirroring
``digest_runner``'s permissive parser.

Multiple classifiers compose into a ``CompositeClassifier`` ordered
by priority: deterministic rules (low priority number) fire first
and outrank the LLM judge on a tie (per F3.5 "deterministic rules
beat LLM if both fire"). The composite is what the brain registers
in production — v1 has exactly one classifier in the list (the LLM
judge at priority ``100``), but the v1.x declarative-classifier
loader can register additional classifiers at lower priorities
without touching ``thread.send`` or ``project.classify``.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from thalyn_brain.projects import Project
from thalyn_brain.provider import ChatTextChunk, LlmProvider

logger = logging.getLogger(__name__)

DEFAULT_CONFIDENCE_THRESHOLD = 0.7

DEFAULT_LLM_PRIORITY = 100
"""Priority of the v1 default LLM judge inside the composite. v1.x
declarative classifiers will register at lower numbers so deterministic
rules outrank the LLM on a tie (per F3.5)."""

CLASSIFIER_SYSTEM_PROMPT = """You are the project classifier for the eternal chat thread.

The user just sent the message below. Decide which of the listed
projects the message belongs to, if any. Return ONLY a single JSON
object of this shape:

{
  "projectId": "<one of the listed projectIds, or null>",
  "confidence": 0.0,
  "reasoning": "<one short sentence>"
}

Confidence is a float in [0.0, 1.0]. Use null for ``projectId`` and
a low confidence when the message doesn't clearly map to any
listed project — that's the safe default; a wrong tag is worse
than no tag.
"""

ClassifierMode = Literal["suggest", "auto"]
"""F3.5: ``suggest`` only emits a recommendation; ``auto`` is the
caller-trusted mode the renderer uses to actually populate
``THREAD_TURN.project_id`` without user confirmation. The
classifier itself doesn't act on the mode — the caller does. We
record it on the verdict so audit trails know whether a turn was
auto-tagged or surfaced for review."""


_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


@dataclass(frozen=True)
class ClassifierVerdict:
    """One classifier's call: pick a project and how sure it is.

    A ``project_id`` of ``None`` means the classifier explicitly
    declined to choose — the caller leaves the turn untagged. A
    project_id with confidence below the threshold is the same
    decline shape; ``classify_into`` collapses both paths.
    """

    project_id: str | None
    confidence: float
    reasoning: str

    def to_wire(self) -> dict[str, object]:
        return {
            "projectId": self.project_id,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
        }


class Classifier(Protocol):
    """Sequence-in, verdict-out — synchronous shape over an async
    transport.

    Implementations are free to call providers, run regexes, or load
    declarative rules; the protocol just standardises the input /
    output shape so v1.x's user-supplied classifiers (F3.5) drop
    into the same wiring without touching ``thread.send``.
    """

    async def classify(
        self,
        message: str,
        candidates: Sequence[Project],
        *,
        foreground_project_id: str | None = None,
    ) -> ClassifierVerdict: ...


class LlmJudgeClassifier:
    """Default v1 classifier — prompts the brain provider for a JSON
    verdict and parses it permissively.

    The judge sees the candidate projects' name + slug + roadmap
    one-liner so it can reason about which project this message
    belongs to. The prompt is intentionally tight so the model has
    no excuse to ramble — JSON only, low confidence on uncertainty.
    """

    def __init__(
        self,
        provider: LlmProvider,
        *,
        threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    ) -> None:
        self._provider = provider
        self._threshold = threshold

    @property
    def threshold(self) -> float:
        return self._threshold

    async def classify(
        self,
        message: str,
        candidates: Sequence[Project],
        *,
        foreground_project_id: str | None = None,
    ) -> ClassifierVerdict:
        if not candidates:
            return ClassifierVerdict(
                project_id=None,
                confidence=0.0,
                reasoning="no candidate projects",
            )
        # When there's only one candidate the model has nothing to
        # judge — short-circuit to keep latency off the hot path.
        if len(candidates) == 1:
            sole = candidates[0]
            return ClassifierVerdict(
                project_id=sole.project_id,
                confidence=1.0,
                reasoning=f"only one active project ({sole.name})",
            )

        prompt = _build_prompt(message, candidates, foreground_project_id)
        try:
            text = await _collect_text(self._provider, prompt)
        except Exception as exc:
            logger.warning("classifier provider call failed: %s", exc)
            return ClassifierVerdict(
                project_id=None,
                confidence=0.0,
                reasoning=f"provider error: {exc}",
            )
        verdict = _parse_verdict(text, valid_ids={p.project_id for p in candidates})
        if verdict is None:
            return ClassifierVerdict(
                project_id=None,
                confidence=0.0,
                reasoning="classifier reply was not valid JSON",
            )
        return verdict


@dataclass(frozen=True)
class RegisteredClassifier:
    """One entry in the composite's priority list.

    ``priority`` is an integer; lower numbers fire first. Two
    classifiers with the same priority compose in registration order
    — the caller is expected to give registered classifiers distinct
    priorities, but ties are tolerated so the composite is robust to
    declarative-classifier config that happens to collide.

    ``name`` is a short identifier (the classifier's class name by
    default) that flows into the verdict's reasoning string when the
    composite picks a non-LLM verdict — so the audit trail shows
    *which* classifier won.
    """

    classifier: Classifier
    priority: int
    name: str


class CompositeClassifier:
    """Ordered priority cascade over a set of classifiers.

    The cascade is "first confident wins": iterate in priority order
    (low → high), call each classifier, and return the first verdict
    that picks a candidate with ``confidence >= threshold``. When no
    classifier reaches threshold, fall through to the last (highest-
    priority-number, typically the LLM judge) classifier's verdict so
    the caller sees the most informative reasoning string for an
    ambiguous turn.

    This shape implements F3.5's "deterministic rules beat LLM if
    both fire": a deterministic classifier registered at priority 10
    outranks an LLM judge at priority 100 whenever the deterministic
    rule produces a confident pick.
    """

    def __init__(
        self,
        entries: Sequence[RegisteredClassifier],
        *,
        threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    ) -> None:
        if not entries:
            raise ValueError("CompositeClassifier requires at least one entry")
        # Stable sort: ties preserve registration order. Mostly cosmetic
        # since v1 has one entry; matters when v1.x loads several.
        self._entries: tuple[RegisteredClassifier, ...] = tuple(
            sorted(entries, key=lambda e: e.priority)
        )
        self._threshold = threshold

    @property
    def entries(self) -> tuple[RegisteredClassifier, ...]:
        return self._entries

    async def classify(
        self,
        message: str,
        candidates: Sequence[Project],
        *,
        foreground_project_id: str | None = None,
    ) -> ClassifierVerdict:
        last_verdict: ClassifierVerdict | None = None
        candidate_ids = {p.project_id for p in candidates}
        for entry in self._entries:
            verdict = await entry.classifier.classify(
                message,
                candidates,
                foreground_project_id=foreground_project_id,
            )
            last_verdict = verdict
            if (
                verdict.project_id is not None
                and verdict.project_id in candidate_ids
                and verdict.confidence >= self._threshold
            ):
                # Stamp the reasoning with the winning classifier so
                # the audit trail can attribute the decision.
                return ClassifierVerdict(
                    project_id=verdict.project_id,
                    confidence=verdict.confidence,
                    reasoning=f"[{entry.name}] {verdict.reasoning}".strip(),
                )
        # No classifier reached threshold; surface the last verdict so
        # the caller sees the most informative reasoning. The caller's
        # threshold check still applies, so a non-confident verdict
        # collapses to the foreground bias in classify_for_routing.
        assert last_verdict is not None  # constructor enforces non-empty
        return last_verdict


async def classify_for_routing(
    classifier: Classifier | None,
    message: str,
    candidates: Sequence[Project],
    *,
    foreground_project_id: str | None,
    threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
) -> str | None:
    """Resolve the project a turn should be tagged to.

    Routing precedence (per F3.7's "foreground attention" model):

    1. ``foreground_project_id`` is the renderer's declared focus —
       respected when the classifier doesn't outright override.
    2. The classifier returns a confident verdict for one of the
       candidates → use that. The classifier is the conservative
       voice; if it doesn't reach ``threshold`` confidence the
       foreground stays sticky.
    3. With no foreground and no confident verdict, the turn lands
       untagged (``project_id=None``).

    The caller still owns the addressed-lead path (``@Lead-X``
    mentions) — that wins over both the classifier and the
    foreground bias because the user explicitly named the lead.
    """
    if not candidates:
        return foreground_project_id
    candidate_ids = {p.project_id for p in candidates}
    if classifier is None:
        # Without a classifier wired (narrow tests), the foreground
        # bias is the only signal we have. A foreground project that
        # isn't in the active candidate set falls through to None so
        # we don't tag a turn against an archived row.
        return foreground_project_id if foreground_project_id in candidate_ids else None
    verdict = await classifier.classify(
        message, candidates, foreground_project_id=foreground_project_id
    )
    if (
        verdict.project_id is not None
        and verdict.project_id in candidate_ids
        and verdict.confidence >= threshold
    ):
        return verdict.project_id
    if foreground_project_id in candidate_ids:
        return foreground_project_id
    return None


def _build_prompt(
    message: str,
    candidates: Sequence[Project],
    foreground_project_id: str | None,
) -> str:
    lines = ["User message:", message.strip(), "", "Candidate projects:"]
    for project in candidates:
        roadmap_blurb = (project.roadmap or "").strip().splitlines()[0:1]
        first_line = roadmap_blurb[0] if roadmap_blurb else "(no roadmap noted)"
        marker = " [foreground]" if project.project_id == foreground_project_id else ""
        lines.append(
            f"- projectId={project.project_id} · name={project.name} · "
            f"slug={project.slug}{marker} · {first_line}"
        )
    lines.extend(
        [
            "",
            "Pick the projectId this message belongs to, or null if none is a confident match.",
        ]
    )
    return "\n".join(lines)


async def _collect_text(provider: LlmProvider, user_message: str) -> str:
    parts: list[str] = []
    async for chunk in provider.stream_chat(user_message, system_prompt=CLASSIFIER_SYSTEM_PROMPT):
        if isinstance(chunk, ChatTextChunk):
            parts.append(chunk.delta)
    return "".join(parts)


def _parse_verdict(text: str, *, valid_ids: set[str]) -> ClassifierVerdict | None:
    match = _JSON_OBJECT_RE.search(text)
    if not match:
        return None
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    project_id_raw = payload.get("projectId")
    project_id = project_id_raw if isinstance(project_id_raw, str) else None
    # An invented project id (model hallucinated) collapses to None
    # so the caller's threshold check doesn't accept a non-existent
    # row.
    if project_id is not None and project_id not in valid_ids:
        project_id = None
    confidence_raw = payload.get("confidence")
    if isinstance(confidence_raw, int | float):
        confidence = max(0.0, min(1.0, float(confidence_raw)))
    else:
        confidence = 0.0
    reasoning_raw = payload.get("reasoning")
    reasoning = reasoning_raw if isinstance(reasoning_raw, str) else ""
    return ClassifierVerdict(
        project_id=project_id,
        confidence=confidence,
        reasoning=reasoning,
    )


__all__ = [
    "CLASSIFIER_SYSTEM_PROMPT",
    "DEFAULT_CONFIDENCE_THRESHOLD",
    "DEFAULT_LLM_PRIORITY",
    "Classifier",
    "ClassifierMode",
    "ClassifierVerdict",
    "CompositeClassifier",
    "LlmJudgeClassifier",
    "RegisteredClassifier",
    "classify_for_routing",
]
