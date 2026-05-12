"""Tests for ``project_classifier``.

Covers the routing-precedence helper (``classify_for_routing``), the
default ``LlmJudgeClassifier`` parsing (including the permissive-on-
junk path that mirrors ``digest_runner``), and the
``CompositeClassifier`` priority cascade (a fake ``RegexClassifier``
proves the ``Classifier`` Protocol is wired through end-to-end).
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Sequence
from typing import Any, cast

from thalyn_brain.project_classifier import (
    ClassifierVerdict,
    CompositeClassifier,
    LlmJudgeClassifier,
    RegisteredClassifier,
    classify_for_routing,
)
from thalyn_brain.projects import Project, new_project_id
from thalyn_brain.provider import (
    ChatChunk,
    ChatStartChunk,
    ChatStopChunk,
    ChatTextChunk,
    LlmProvider,
)


def _project(name: str, slug: str | None = None, *, roadmap: str = "") -> Project:
    now = int(time.time() * 1000)
    return Project(
        project_id=new_project_id(),
        name=name,
        slug=slug or name.lower().replace(" ", "-"),
        workspace_path=None,
        repo_remote=None,
        lead_agent_id=None,
        memory_namespace=slug or name.lower(),
        conversation_tag=name,
        roadmap=roadmap,
        provider_config=None,
        connector_grants=None,
        local_only=False,
        status="active",
        created_at_ms=now,
        last_active_at_ms=now,
    )


class _ScriptedProvider:
    """Provider stub that yields the supplied text in one chunk."""

    def __init__(self, text: str) -> None:
        self._text = text
        self.last_prompt: str | None = None
        self.last_system_prompt: str | None = None

    def stream_chat(
        self,
        prompt: str,
        *,
        system_prompt: str = "",
        **_: Any,
    ) -> AsyncIterator[ChatChunk]:
        self.last_prompt = prompt
        self.last_system_prompt = system_prompt

        async def gen() -> AsyncIterator[ChatChunk]:
            yield ChatStartChunk(model="fake")
            yield ChatTextChunk(delta=self._text)
            yield ChatStopChunk(reason="end_turn")

        return gen()


class _StubClassifier:
    """Returns a fixed verdict regardless of input."""

    def __init__(self, verdict: ClassifierVerdict) -> None:
        self._verdict = verdict
        self.calls: list[tuple[str, Sequence[Project], str | None]] = []

    async def classify(
        self,
        message: str,
        candidates: Sequence[Project],
        *,
        foreground_project_id: str | None = None,
    ) -> ClassifierVerdict:
        self.calls.append((message, list(candidates), foreground_project_id))
        return self._verdict


# ---------- classify_for_routing precedence ----------


async def test_routing_returns_foreground_when_no_classifier() -> None:
    foreground = _project("Foreground")
    other = _project("Other")
    resolved = await classify_for_routing(
        None,
        "anything",
        [foreground, other],
        foreground_project_id=foreground.project_id,
    )
    assert resolved == foreground.project_id


async def test_routing_drops_foreground_not_in_candidate_set() -> None:
    other = _project("Other")
    resolved = await classify_for_routing(
        None,
        "anything",
        [other],
        foreground_project_id="proj_archived",
    )
    assert resolved is None


async def test_routing_returns_classifier_choice_above_threshold() -> None:
    target = _project("Target")
    foreground = _project("Foreground")
    classifier = _StubClassifier(
        ClassifierVerdict(project_id=target.project_id, confidence=0.85, reasoning="match")
    )
    resolved = await classify_for_routing(
        classifier,
        "this is about target",
        [foreground, target],
        foreground_project_id=foreground.project_id,
        threshold=0.7,
    )
    assert resolved == target.project_id


async def test_routing_keeps_foreground_below_threshold() -> None:
    target = _project("Target")
    foreground = _project("Foreground")
    classifier = _StubClassifier(
        ClassifierVerdict(project_id=target.project_id, confidence=0.4, reasoning="weak")
    )
    resolved = await classify_for_routing(
        classifier,
        "ambiguous",
        [foreground, target],
        foreground_project_id=foreground.project_id,
        threshold=0.7,
    )
    assert resolved == foreground.project_id


async def test_routing_returns_none_when_no_foreground_and_low_confidence() -> None:
    target = _project("Target")
    other = _project("Other")
    classifier = _StubClassifier(
        ClassifierVerdict(project_id=target.project_id, confidence=0.4, reasoning="weak")
    )
    resolved = await classify_for_routing(
        classifier,
        "ambiguous",
        [other, target],
        foreground_project_id=None,
        threshold=0.7,
    )
    assert resolved is None


async def test_routing_drops_classifier_choice_outside_candidate_set() -> None:
    target = _project("Target")
    foreground = _project("Foreground")
    classifier = _StubClassifier(
        ClassifierVerdict(project_id="proj_invented", confidence=0.95, reasoning="hallucinated")
    )
    resolved = await classify_for_routing(
        classifier,
        "anything",
        [foreground, target],
        foreground_project_id=foreground.project_id,
    )
    # Falls back to foreground rather than honouring the bogus id.
    assert resolved == foreground.project_id


# ---------- LlmJudgeClassifier behaviour ----------


async def test_judge_short_circuits_on_single_candidate() -> None:
    sole = _project("Sole")
    judge = LlmJudgeClassifier(cast(LlmProvider, _ScriptedProvider("ignored")))
    verdict = await judge.classify("anything", [sole])
    assert verdict.project_id == sole.project_id
    assert verdict.confidence == 1.0


async def test_judge_returns_none_with_no_candidates() -> None:
    judge = LlmJudgeClassifier(cast(LlmProvider, _ScriptedProvider("ignored")))
    verdict = await judge.classify("anything", [])
    assert verdict.project_id is None
    assert verdict.confidence == 0.0


async def test_judge_parses_well_formed_json_verdict() -> None:
    target = _project("Target")
    other = _project("Other")
    payload = (
        '{"projectId": "'
        + target.project_id
        + '", "confidence": 0.92, "reasoning": "explicit reference"}'
    )
    provider = _ScriptedProvider(payload)
    judge = LlmJudgeClassifier(cast(LlmProvider, provider))
    verdict = await judge.classify("about target", [other, target])
    assert verdict.project_id == target.project_id
    assert verdict.confidence == 0.92
    assert verdict.reasoning == "explicit reference"
    assert provider.last_system_prompt is not None


async def test_judge_collapses_invalid_id_to_none() -> None:
    target = _project("Target")
    other = _project("Other")
    payload = '{"projectId": "proj_invented", "confidence": 0.95}'
    judge = LlmJudgeClassifier(cast(LlmProvider, _ScriptedProvider(payload)))
    verdict = await judge.classify("anything", [other, target])
    assert verdict.project_id is None


async def test_judge_returns_low_confidence_when_unparseable() -> None:
    target = _project("Target")
    other = _project("Other")
    judge = LlmJudgeClassifier(cast(LlmProvider, _ScriptedProvider("not even json")))
    verdict = await judge.classify("anything", [other, target])
    assert verdict.project_id is None
    assert verdict.confidence == 0.0
    assert "not valid JSON" in verdict.reasoning


async def test_judge_clamps_out_of_range_confidence() -> None:
    target = _project("Target")
    other = _project("Other")
    payload = '{"projectId": "' + target.project_id + '", "confidence": 1.7, "reasoning": "x"}'
    judge = LlmJudgeClassifier(cast(LlmProvider, _ScriptedProvider(payload)))
    verdict = await judge.classify("anything", [other, target])
    assert verdict.project_id == target.project_id
    assert verdict.confidence == 1.0


async def test_judge_handles_provider_exception_as_low_confidence() -> None:
    target = _project("Target")
    other = _project("Other")

    class _BoomProvider:
        def stream_chat(self, *_args: Any, **_kwargs: Any) -> AsyncIterator[ChatChunk]:
            async def gen() -> AsyncIterator[ChatChunk]:
                # An async generator that raises before yielding is
                # exactly the iterator shape the classifier handles —
                # the raise fires on the first ``__anext__`` call.
                raise RuntimeError("provider down")
                yield ChatStartChunk(model="fake")  # type: ignore[unreachable]

            return gen()

    judge = LlmJudgeClassifier(cast(LlmProvider, _BoomProvider()))
    verdict = await judge.classify("anything", [other, target])
    assert verdict.project_id is None
    assert verdict.confidence == 0.0
    assert "provider down" in verdict.reasoning


# ---------- CompositeClassifier (pluggable interface) ----------


class _RegexClassifier:
    """Deterministic classifier that fires on a regex match.

    Used to prove the ``Classifier`` Protocol is the real interface
    — the composite routes through it identically to the LLM judge,
    and (per F3.5) outranks the LLM judge whenever both match.
    """

    def __init__(self, pattern: str, project_resolver: dict[str, Project]) -> None:
        import re

        self._re = re.compile(pattern, re.IGNORECASE)
        self._project_resolver = project_resolver
        self.calls = 0

    async def classify(
        self,
        message: str,
        candidates: Sequence[Project],
        *,
        foreground_project_id: str | None = None,
    ) -> ClassifierVerdict:
        self.calls += 1
        match = self._re.search(message)
        if match is None:
            return ClassifierVerdict(
                project_id=None,
                confidence=0.0,
                reasoning="regex did not match",
            )
        candidate_key = match.group(0).lower()
        candidate = self._project_resolver.get(candidate_key)
        if candidate is None:
            return ClassifierVerdict(
                project_id=None,
                confidence=0.0,
                reasoning=f"regex matched '{candidate_key}' but no project mapped",
            )
        return ClassifierVerdict(
            project_id=candidate.project_id,
            confidence=1.0,
            reasoning=f"regex matched '{candidate_key}'",
        )


async def test_composite_returns_higher_priority_confident_verdict() -> None:
    ui = _project("UI")
    thalyn = _project("Thalyn")
    regex = _RegexClassifier(r"ui", project_resolver={"ui": ui})
    llm = _StubClassifier(
        ClassifierVerdict(
            project_id=thalyn.project_id, confidence=0.9, reasoning="LLM thinks Thalyn"
        ),
    )
    composite = CompositeClassifier(
        entries=(
            RegisteredClassifier(classifier=regex, priority=10, name="regex"),
            RegisteredClassifier(classifier=llm, priority=100, name="llm-judge"),
        ),
    )
    verdict = await composite.classify("this is about ui", [ui, thalyn])
    # Deterministic rule wins even though LLM is also confident.
    assert verdict.project_id == ui.project_id
    assert verdict.confidence == 1.0
    assert "[regex]" in verdict.reasoning


async def test_composite_falls_through_to_llm_when_regex_misses() -> None:
    ui = _project("UI")
    thalyn = _project("Thalyn")
    regex = _RegexClassifier(r"\bnonsense\b", project_resolver={"nonsense": ui})
    llm = _StubClassifier(
        ClassifierVerdict(
            project_id=thalyn.project_id, confidence=0.85, reasoning="LLM picked Thalyn"
        ),
    )
    composite = CompositeClassifier(
        entries=(
            RegisteredClassifier(classifier=regex, priority=10, name="regex"),
            RegisteredClassifier(classifier=llm, priority=100, name="llm-judge"),
        ),
    )
    verdict = await composite.classify("nothing matching here", [ui, thalyn])
    assert verdict.project_id == thalyn.project_id
    assert verdict.confidence == 0.85
    assert "[llm-judge]" in verdict.reasoning


async def test_composite_returns_last_verdict_when_no_one_confident() -> None:
    ui = _project("UI")
    thalyn = _project("Thalyn")
    regex = _RegexClassifier(r"\bnonsense\b", project_resolver={"nonsense": ui})
    llm = _StubClassifier(
        ClassifierVerdict(project_id=ui.project_id, confidence=0.3, reasoning="weak guess"),
    )
    composite = CompositeClassifier(
        entries=(
            RegisteredClassifier(classifier=regex, priority=10, name="regex"),
            RegisteredClassifier(classifier=llm, priority=100, name="llm-judge"),
        ),
    )
    verdict = await composite.classify("ambiguous prompt", [ui, thalyn])
    # No classifier reached threshold — the LLM's verdict surfaces
    # (unannotated, so the caller's threshold check collapses it to
    # the foreground bias in classify_for_routing).
    assert verdict.project_id == ui.project_id
    assert verdict.confidence == 0.3
    assert "weak guess" in verdict.reasoning


async def test_composite_ties_break_on_registration_order() -> None:
    ui = _project("UI")
    thalyn = _project("Thalyn")
    a = _StubClassifier(
        ClassifierVerdict(project_id=ui.project_id, confidence=0.9, reasoning="A says UI"),
    )
    b = _StubClassifier(
        ClassifierVerdict(project_id=thalyn.project_id, confidence=0.9, reasoning="B says Thalyn"),
    )
    composite = CompositeClassifier(
        entries=(
            RegisteredClassifier(classifier=a, priority=50, name="a"),
            RegisteredClassifier(classifier=b, priority=50, name="b"),
        ),
    )
    verdict = await composite.classify("anything", [ui, thalyn])
    # A registered first; the stable sort preserves order on ties.
    assert verdict.project_id == ui.project_id


async def test_composite_requires_at_least_one_entry() -> None:
    import pytest

    with pytest.raises(ValueError, match="at least one entry"):
        CompositeClassifier(entries=())


async def test_composite_routes_through_classify_for_routing() -> None:
    """End-to-end smoke: classify_for_routing accepts a composite via
    the Classifier Protocol — no special-casing — and the deterministic
    classifier still wins."""
    ui = _project("UI")
    thalyn = _project("Thalyn")
    regex = _RegexClassifier(r"ui", project_resolver={"ui": ui})
    llm = _StubClassifier(
        ClassifierVerdict(project_id=thalyn.project_id, confidence=0.85, reasoning="LLM"),
    )
    composite = CompositeClassifier(
        entries=(
            RegisteredClassifier(classifier=regex, priority=10, name="regex"),
            RegisteredClassifier(classifier=llm, priority=100, name="llm"),
        ),
    )
    resolved = await classify_for_routing(
        composite,
        "this concerns ui work",
        [ui, thalyn],
        foreground_project_id=thalyn.project_id,
    )
    # Foreground was Thalyn; regex confidently picks UI; UI wins.
    assert resolved == ui.project_id
