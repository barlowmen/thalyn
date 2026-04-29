"""End-to-end test for the chat.send → run-through-lead path.

When the renderer drives ``chat.send`` with a ``leadId`` param, the
runner records ``agent_id = leadId`` (the lead is what's running),
``parent_lead_id = leadId`` (the run belongs to the lead's tree),
and every spawned worker inherits the lead-tier attribution. The
exit criterion the phase calls for — "spawns a worker through the
lead, completes the run, and surfaces the lead's report" — is
covered here at the brain layer.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from thalyn_brain.agents import AgentRecordsStore
from thalyn_brain.approval_rpc import register_approval_methods
from thalyn_brain.chat import register_chat_methods
from thalyn_brain.lead_lifecycle import LeadLifecycle, SpawnRequest
from thalyn_brain.orchestration import Runner
from thalyn_brain.orchestration.state import RunStatus
from thalyn_brain.projects import Project, ProjectsStore, new_project_id
from thalyn_brain.provider import AnthropicProvider, ProviderRegistry
from thalyn_brain.rpc import Dispatcher
from thalyn_brain.runs import RunsStore
from thalyn_brain.runs_rpc import register_runs_methods

from tests.provider._fake_sdk import factory_for, result_message, text_message


def _now() -> int:
    return int(time.time() * 1000)


def _registry_with(provider: AnthropicProvider) -> ProviderRegistry:
    registry = ProviderRegistry()
    registry._providers["anthropic"] = provider
    return registry


def _capture() -> tuple[list[tuple[str, Any]], Any]:
    captured: list[tuple[str, Any]] = []

    async def notify(method: str, params: Any) -> None:
        captured.append((method, params))

    return captured, notify


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


def _delegated_plan() -> str:
    return (
        '{"goal": "investigate prior art",'
        ' "steps": ['
        '   {"description": "Audit existing call sites.",'
        '    "rationale": "Need a full picture.",'
        '    "estimated_tokens": 800,'
        '    "subagent_kind": "research"}'
        "]}"
    )


async def test_chat_send_with_lead_tags_run_and_inherits_in_workers(
    tmp_path: Path,
) -> None:
    _fake, factory = factory_for(
        [
            text_message(_delegated_plan()),
            result_message(),
            text_message('{"goal": "x", "steps": []}'),
            result_message(),
            text_message("Investigated the call sites."),
            result_message(total_cost_usd=0.0001),
            text_message("Here's what Sam found."),
            result_message(total_cost_usd=0.0002),
        ]
    )
    provider = AnthropicProvider(client_factory=factory)
    registry = _registry_with(provider)

    runs_store = RunsStore(data_dir=tmp_path)
    agents = AgentRecordsStore(data_dir=tmp_path)
    projects = ProjectsStore(data_dir=tmp_path)
    lifecycle = LeadLifecycle(agents=agents, projects=projects)
    project = await _seed_project(projects)
    lead = await lifecycle.spawn(
        SpawnRequest(project_id=project.project_id, display_name="Sam"),
    )

    runner = Runner(registry, runs_store=runs_store, data_dir=tmp_path)
    dispatcher = Dispatcher()
    register_chat_methods(dispatcher, registry, runner=runner)
    register_approval_methods(dispatcher, runner)
    register_runs_methods(dispatcher, runs_store, runner=runner)

    _captured, notify = _capture()
    send_response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "chat.send",
            "params": {
                "sessionId": "sess",
                "providerId": "anthropic",
                "prompt": "Sam, audit the call sites.",
                "leadId": lead.agent_id,
            },
        },
        notify,
    )
    assert send_response is not None
    send_result = send_response["result"]
    root_run_id = send_result["runId"]
    assert send_result["status"] == RunStatus.AWAITING_APPROVAL.value
    assert send_result["leadId"] == lead.agent_id

    approve_response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "run.approve_plan",
            "params": {
                "runId": root_run_id,
                "providerId": "anthropic",
                "decision": "approve",
            },
        },
        notify,
    )
    assert approve_response is not None
    assert approve_response["result"]["status"] == RunStatus.COMPLETED.value

    # Root run carries the lead as both agent_id and parent_lead_id.
    headers = await runs_store.list_runs()
    by_id = {h.run_id: h for h in headers}
    root = by_id[root_run_id]
    assert root.agent_id == lead.agent_id
    assert root.parent_lead_id == lead.agent_id

    # Every spawned worker (descendant) inherits parent_lead_id even
    # though agent_id stays NULL — workers are ephemeral and not in
    # agent_records.
    descendants = await runs_store.list_descendants(root_run_id)
    children = [h for h in descendants if h.run_id != root_run_id]
    assert len(children) == 1
    child = children[0]
    assert child.parent_lead_id == lead.agent_id
    assert child.agent_id is None

    # The drill query (parent_lead_id filter) sees the whole tree.
    by_lead = await runs_store.list_runs(parent_lead_id=lead.agent_id)
    by_lead_ids = {h.run_id for h in by_lead}
    assert root_run_id in by_lead_ids
    assert child.run_id in by_lead_ids


async def test_chat_send_without_lead_leaves_attribution_null(
    tmp_path: Path,
) -> None:
    """No leadId param → run.agent_id and parent_lead_id stay NULL.
    The legacy v1 behaviour for project-less prompts."""
    _fake, factory = factory_for([text_message('{"goal": "Hi", "steps": []}'), result_message()])
    provider = AnthropicProvider(client_factory=factory)
    registry = _registry_with(provider)

    runs_store = RunsStore(data_dir=tmp_path)
    runner = Runner(registry, runs_store=runs_store, data_dir=tmp_path)
    dispatcher = Dispatcher()
    register_chat_methods(dispatcher, registry, runner=runner)

    _, notify = _capture()
    response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "chat.send",
            "params": {
                "sessionId": "sess",
                "providerId": "anthropic",
                "prompt": "Hi",
            },
        },
        notify,
    )
    assert response is not None
    assert "leadId" not in response["result"]

    headers = await runs_store.list_runs()
    assert len(headers) == 1
    assert headers[0].agent_id is None
    assert headers[0].parent_lead_id is None
