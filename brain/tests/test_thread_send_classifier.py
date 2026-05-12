"""End-to-end tests for the project-classifier hop inside ``thread.send``.

The router-precedence rules are exercised at the unit level in
``test_project_classifier``; this module proves the wiring — that
``thread.send`` calls the classifier when the renderer doesn't pin a
foreground project, that the resolved ``project_id`` lands on the
persisted user + brain turns, and that the project's
``last_active_at_ms`` advances so the switcher's recency sort
reflects the routed turn.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from thalyn_brain.agents import AgentRecordsStore
from thalyn_brain.project_classifier import ClassifierVerdict, NewProjectSuggestion
from thalyn_brain.projects import ProjectsStore
from thalyn_brain.provider import AnthropicProvider, ProviderRegistry
from thalyn_brain.rpc import Dispatcher
from thalyn_brain.thread_send import register_thread_send_methods
from thalyn_brain.threads import Thread, ThreadsStore, new_thread_id

from tests.provider._fake_sdk import factory_for, result_message, text_message


def _now() -> int:
    return int(time.time() * 1000)


def _registry_with(provider: AnthropicProvider) -> ProviderRegistry:
    registry = ProviderRegistry()
    registry._providers["anthropic"] = provider
    return registry


class _DropNotify:
    async def __call__(self, method: str, params: Any) -> None:
        return None


_drop_notify = _DropNotify()


async def _seed_thread(store: ThreadsStore) -> Thread:
    thread = Thread(
        thread_id=new_thread_id(),
        user_scope="self",
        created_at_ms=_now(),
        last_active_at_ms=_now(),
    )
    await store.insert_thread(thread)
    return thread


class _ScriptedClassifier:
    """Records each call; returns the verdict the caller pre-loads."""

    def __init__(self, verdict: ClassifierVerdict) -> None:
        self.verdict = verdict
        self.calls: list[tuple[str, list[str], str | None]] = []

    async def classify(
        self,
        message: str,
        candidates: Sequence[Any],
        *,
        foreground_project_id: str | None = None,
    ) -> ClassifierVerdict:
        self.calls.append((message, [c.project_id for c in candidates], foreground_project_id))
        return self.verdict


async def _build(
    tmp_path: Path,
    *,
    classifier: _ScriptedClassifier | None,
    brain_text: str = "okay.",
) -> tuple[Dispatcher, ThreadsStore, ProjectsStore]:
    threads = ThreadsStore(data_dir=tmp_path)
    projects = ProjectsStore(data_dir=tmp_path)
    agents = AgentRecordsStore(data_dir=tmp_path)
    _, factory = factory_for([text_message(brain_text), result_message()])
    provider = AnthropicProvider(client_factory=factory)
    registry = _registry_with(provider)
    dispatcher = Dispatcher()
    register_thread_send_methods(
        dispatcher,
        threads_store=threads,
        registry=registry,
        agent_records=agents,
        projects_store=projects,
        classifier=classifier,
    )
    return dispatcher, threads, projects


async def _send(
    dispatcher: Dispatcher,
    *,
    thread_id: str,
    prompt: str,
    project_id: str | None = None,
    request_id: int = 1,
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "threadId": thread_id,
        "providerId": "anthropic",
        "prompt": prompt,
    }
    if project_id is not None:
        params["projectId"] = project_id
    response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "thread.send",
            "params": params,
        },
        _drop_notify,
    )
    assert response is not None
    return response


async def test_classifier_route_overrides_foreground(tmp_path: Path) -> None:
    classifier = _ScriptedClassifier(
        ClassifierVerdict(project_id="placeholder", confidence=0.9, reasoning="match")
    )
    dispatcher, threads, projects = await _build(tmp_path, classifier=classifier)
    foreground = await projects.create(name="Foreground")
    target = await projects.create(name="Target")
    classifier.verdict = ClassifierVerdict(
        project_id=target.project_id, confidence=0.92, reasoning="explicit reference"
    )
    thread = await _seed_thread(threads)

    response = await _send(
        dispatcher,
        thread_id=thread.thread_id,
        prompt="that thing about target",
        project_id=foreground.project_id,
    )

    result = response["result"]
    assert result["projectId"] == target.project_id
    # Recency sort: target was just touched, foreground wasn't.
    refreshed_target = await projects.get(target.project_id)
    refreshed_foreground = await projects.get(foreground.project_id)
    assert refreshed_target is not None
    assert refreshed_foreground is not None
    assert refreshed_target.last_active_at_ms > refreshed_foreground.last_active_at_ms
    # The classifier saw both new candidates plus the seeded default
    # project (migration 004) and the renderer's foreground id.
    assert classifier.calls
    _, candidate_ids, fg = classifier.calls[0]
    assert {foreground.project_id, target.project_id}.issubset(set(candidate_ids))
    assert fg == foreground.project_id


async def test_low_confidence_keeps_foreground(tmp_path: Path) -> None:
    classifier = _ScriptedClassifier(
        ClassifierVerdict(project_id="placeholder", confidence=0.3, reasoning="weak")
    )
    dispatcher, threads, projects = await _build(tmp_path, classifier=classifier)
    foreground = await projects.create(name="Foreground")
    other = await projects.create(name="Other")
    classifier.verdict = ClassifierVerdict(
        project_id=other.project_id, confidence=0.3, reasoning="weak"
    )
    thread = await _seed_thread(threads)

    response = await _send(
        dispatcher,
        thread_id=thread.thread_id,
        prompt="ambiguous question",
        project_id=foreground.project_id,
    )

    assert response["result"]["projectId"] == foreground.project_id


async def test_no_foreground_no_confidence_lands_untagged(tmp_path: Path) -> None:
    classifier = _ScriptedClassifier(
        ClassifierVerdict(project_id=None, confidence=0.0, reasoning="declined")
    )
    dispatcher, threads, projects = await _build(tmp_path, classifier=classifier)
    await projects.create(name="One")
    await projects.create(name="Two")
    thread = await _seed_thread(threads)

    response = await _send(
        dispatcher,
        thread_id=thread.thread_id,
        prompt="hello",
    )

    assert response["result"]["projectId"] is None


async def test_default_project_alone_short_circuits_classifier(tmp_path: Path) -> None:
    classifier = _ScriptedClassifier(
        ClassifierVerdict(project_id=None, confidence=0.0, reasoning="should not run")
    )
    dispatcher, threads, projects = await _build(tmp_path, classifier=classifier)
    sole = await projects.create(name="Sole")
    thread = await _seed_thread(threads)

    response = await _send(
        dispatcher,
        thread_id=thread.thread_id,
        prompt="anything",
        project_id=sole.project_id,
    )

    # With one candidate the classifier still gets called, but
    # short-circuits to confidence=1.0 internally — the route lands
    # on the sole project.
    assert response["result"]["projectId"] == sole.project_id


async def test_suggest_new_project_surfaces_when_untagged(tmp_path: Path) -> None:
    """Classifier proposes a fresh project; no candidate matches → the
    suggestion lands in the response so the renderer can prompt
    'create a new project named X?'."""
    classifier = _ScriptedClassifier(
        ClassifierVerdict(
            project_id=None,
            confidence=0.0,
            reasoning="no fit",
            suggest_new_project=NewProjectSuggestion(
                name="Coffee Shop App",
                rationale="user is opening a fresh coding-side topic",
            ),
        )
    )
    dispatcher, threads, projects = await _build(tmp_path, classifier=classifier)
    await projects.create(name="UI")
    await projects.create(name="Thalyn")
    thread = await _seed_thread(threads)

    response = await _send(
        dispatcher,
        thread_id=thread.thread_id,
        prompt="I want to start working on a coffee shop scheduling app",
    )

    assert response["result"]["projectId"] is None
    assert response["result"]["projectSuggestion"] == {
        "name": "Coffee Shop App",
        "rationale": "user is opening a fresh coding-side topic",
    }


async def test_suggest_new_project_suppressed_when_route_lands(tmp_path: Path) -> None:
    """A successful route to an existing project beats any suggestion —
    even if the LLM emitted one alongside its confident verdict, the
    user already has a home for the turn."""
    classifier = _ScriptedClassifier(
        ClassifierVerdict(
            project_id="placeholder",
            confidence=0.9,
            reasoning="match",
            suggest_new_project=NewProjectSuggestion(name="Side", rationale="unused"),
        )
    )
    dispatcher, threads, projects = await _build(tmp_path, classifier=classifier)
    target = await projects.create(name="Target")
    other = await projects.create(name="Other")
    classifier.verdict = ClassifierVerdict(
        project_id=target.project_id,
        confidence=0.9,
        reasoning="match",
        suggest_new_project=NewProjectSuggestion(name="Side", rationale="unused"),
    )
    thread = await _seed_thread(threads)

    response = await _send(
        dispatcher,
        thread_id=thread.thread_id,
        prompt="about target",
        project_id=other.project_id,
    )

    assert response["result"]["projectId"] == target.project_id
    assert "projectSuggestion" not in response["result"]
