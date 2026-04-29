"""End-to-end test for the conversational routing-edit path inside
``thread.send`` (per ADR-0023).

The brain recognises "route coding to ollama in this project" before
delegating, dispatches the action against ``RoutingOverridesStore``,
and the user sees the dispatcher's confirmation as the brain's
reply. The next routing-table lookup for ``coding`` in that project
returns the new override — exactly what the spawn-time routing layer
will see.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
from thalyn_brain.agents import AgentRecordsStore
from thalyn_brain.projects import Project, ProjectsStore, new_project_id
from thalyn_brain.provider import AnthropicProvider, ProviderRegistry
from thalyn_brain.routing import RoutingOverridesStore
from thalyn_brain.routing_intents import RoutingActionsDispatcher
from thalyn_brain.rpc import Dispatcher
from thalyn_brain.thread_send import register_thread_send_methods
from thalyn_brain.threads import Thread, ThreadsStore, new_thread_id

from tests.provider._fake_sdk import factory_for


def _now() -> int:
    return int(time.time() * 1000)


def _registry_with(provider: AnthropicProvider) -> ProviderRegistry:
    registry = ProviderRegistry()
    registry._providers["anthropic"] = provider
    registry._providers["ollama"] = provider
    return registry


async def _seed_thread(store: ThreadsStore) -> Thread:
    thread = Thread(
        thread_id=new_thread_id(),
        user_scope="self",
        created_at_ms=_now(),
        last_active_at_ms=_now(),
    )
    await store.insert_thread(thread)
    return thread


async def _seed_project(projects: ProjectsStore) -> Project:
    project = Project(
        project_id=new_project_id(),
        name="Alpha",
        slug="alpha",
        workspace_path=None,
        repo_remote=None,
        lead_agent_id=None,
        memory_namespace="alpha",
        conversation_tag="Alpha",
        roadmap="",
        provider_config=None,
        connector_grants=None,
        local_only=False,
        status="active",
        created_at_ms=_now(),
        last_active_at_ms=_now(),
    )
    await projects.insert(project)
    return project


@pytest.mark.asyncio
async def test_thread_send_recognizes_routing_intent_and_writes_override(
    tmp_path: Path,
) -> None:
    threads = ThreadsStore(data_dir=tmp_path)
    agents = AgentRecordsStore(data_dir=tmp_path)
    projects = ProjectsStore(data_dir=tmp_path)
    overrides = RoutingOverridesStore(data_dir=tmp_path)

    _fake, factory = factory_for([])  # No LLM calls expected.
    provider = AnthropicProvider(client_factory=factory)
    registry = _registry_with(provider)
    routing_actions = RoutingActionsDispatcher(
        overrides_store=overrides,
        projects_store=projects,
        valid_provider_ids={"anthropic", "ollama", "mlx"},
    )

    dispatcher = Dispatcher()
    register_thread_send_methods(
        dispatcher,
        threads_store=threads,
        registry=registry,
        agent_records=agents,
        routing_actions=routing_actions,
    )

    thread = await _seed_thread(threads)
    project = await _seed_project(projects)

    captured: list[tuple[str, Any]] = []

    async def notify(method: str, params: Any) -> None:
        captured.append((method, params))

    response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "thread.send",
            "params": {
                "threadId": thread.thread_id,
                "providerId": "anthropic",
                "projectId": project.project_id,
                "prompt": "Thalyn, route coding to ollama in this project.",
            },
        },
        notify,
    )
    assert response is not None
    result = response["result"]
    assert result["routingAction"] == "set"
    assert "ollama" in result["finalResponse"]
    assert result["projectId"] == project.project_id

    # The override landed in the table; a follow-up routing.get / spawn
    # would resolve coding → ollama.
    stored = await overrides.list_for_project(project.project_id)
    assert {(o.task_tag, o.provider_id) for o in stored} == {("coding", "ollama")}

    # The renderer saw a streamed reply: start → text(confirmation) → stop.
    chunk_kinds = [
        params["chunk"]["kind"] for method, params in captured if method == "thread.chunk"
    ]
    assert chunk_kinds == ["start", "text", "stop"]


@pytest.mark.asyncio
async def test_thread_send_falls_through_when_no_intent_recognized(tmp_path: Path) -> None:
    """A non-routing prompt with the dispatcher wired must fall through
    to the regular reply flow — the dispatcher is opt-in matching, not
    a gate over the entire surface."""
    threads = ThreadsStore(data_dir=tmp_path)
    agents = AgentRecordsStore(data_dir=tmp_path)
    projects = ProjectsStore(data_dir=tmp_path)
    overrides = RoutingOverridesStore(data_dir=tmp_path)

    from tests.provider._fake_sdk import result_message, text_message

    _fake, factory = factory_for(
        [
            text_message("Sure, here's a quick answer."),
            result_message(total_cost_usd=0.0001),
        ]
    )
    provider = AnthropicProvider(client_factory=factory)
    registry = _registry_with(provider)
    routing_actions = RoutingActionsDispatcher(
        overrides_store=overrides,
        projects_store=projects,
        valid_provider_ids={"anthropic"},
    )

    dispatcher = Dispatcher()
    register_thread_send_methods(
        dispatcher,
        threads_store=threads,
        registry=registry,
        agent_records=agents,
        routing_actions=routing_actions,
    )

    thread = await _seed_thread(threads)
    project = await _seed_project(projects)

    class _Noop:
        async def __call__(self, method: str, params: Any) -> None:
            return None

    response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "thread.send",
            "params": {
                "threadId": thread.thread_id,
                "providerId": "anthropic",
                "projectId": project.project_id,
                "prompt": "what's the status on the auth refactor?",
            },
        },
        _Noop(),
    )
    assert response is not None
    result = response["result"]
    # Regular reply flow, not a routing action.
    assert "routingAction" not in result
    assert result["finalResponse"] == "Sure, here's a quick answer."
