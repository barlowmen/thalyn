"""Tests for the lead lifecycle RPC surface.

The handlers are thin wrappers over ``LeadLifecycle``; the surface
test asserts every method (a) parses params, (b) translates lifecycle
errors into ``INVALID_PARAMS``, and (c) returns the wire-shape agent
record. The state-machine semantics themselves live in
``test_lead_lifecycle``.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from thalyn_brain.agents import AgentRecordsStore
from thalyn_brain.lead_lifecycle import LeadLifecycle
from thalyn_brain.lead_rpc import DEPTH_CAP_EXCEEDED, register_lead_methods
from thalyn_brain.projects import Project, ProjectsStore, new_project_id
from thalyn_brain.rpc import INVALID_PARAMS, Dispatcher


class _DropNotify:
    async def __call__(self, method: str, params: Any) -> None:
        return None


_drop_notify = _DropNotify()


async def _setup(tmp_path: Path) -> tuple[Dispatcher, LeadLifecycle, ProjectsStore]:
    agents = AgentRecordsStore(data_dir=tmp_path)
    projects = ProjectsStore(data_dir=tmp_path)
    lifecycle = LeadLifecycle(agents=agents, projects=projects)
    dispatcher = Dispatcher()
    register_lead_methods(dispatcher, lifecycle)
    return dispatcher, lifecycle, projects


async def _seed_project(projects: ProjectsStore, *, slug: str = "alpha") -> Project:
    now = int(time.time() * 1000)
    project = Project(
        project_id=new_project_id(),
        name=slug.title(),
        slug=slug,
        workspace_path=None,
        repo_remote=None,
        lead_agent_id=None,
        memory_namespace=slug,
        conversation_tag=slug.title(),
        roadmap="",
        provider_config=None,
        connector_grants=None,
        local_only=False,
        status="active",
        created_at_ms=now,
        last_active_at_ms=now,
    )
    await projects.insert(project)
    return project


async def _call(
    dispatcher: Dispatcher,
    method: str,
    params: dict[str, Any],
    *,
    request_id: int = 1,
) -> dict[str, Any]:
    response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        },
        notify=_drop_notify,
    )
    assert response is not None
    return response


async def test_lead_spawn_returns_wire_record(tmp_path: Path) -> None:
    dispatcher, _lifecycle, projects = await _setup(tmp_path)
    project = await _seed_project(projects, slug="alpha")

    response = await _call(
        dispatcher,
        "lead.spawn",
        {"projectId": project.project_id, "displayName": "Sam"},
    )

    assert "result" in response
    agent = response["result"]["agent"]
    assert agent["kind"] == "lead"
    assert agent["projectId"] == project.project_id
    assert agent["displayName"] == "Sam"
    assert agent["status"] == "active"


async def test_lead_spawn_missing_project_id_returns_invalid_params(
    tmp_path: Path,
) -> None:
    dispatcher, _lifecycle, _projects = await _setup(tmp_path)
    response = await _call(dispatcher, "lead.spawn", {})

    assert "error" in response
    assert response["error"]["code"] == INVALID_PARAMS


async def test_lead_spawn_unknown_project_translates_to_invalid_params(
    tmp_path: Path,
) -> None:
    dispatcher, _lifecycle, _projects = await _setup(tmp_path)
    response = await _call(
        dispatcher,
        "lead.spawn",
        {"projectId": "proj_does_not_exist"},
    )

    assert "error" in response
    assert response["error"]["code"] == INVALID_PARAMS
    assert "does not exist" in response["error"]["message"]


async def test_lead_list_returns_filtered_records(tmp_path: Path) -> None:
    dispatcher, lifecycle, projects = await _setup(tmp_path)
    project_a = await _seed_project(projects, slug="alpha")
    project_b = await _seed_project(projects, slug="beta")
    await _call(dispatcher, "lead.spawn", {"projectId": project_a.project_id})
    spawn_b = await _call(
        dispatcher,
        "lead.spawn",
        {"projectId": project_b.project_id},
        request_id=2,
    )
    lead_b_id = spawn_b["result"]["agent"]["agentId"]
    await lifecycle.pause(lead_b_id)

    response = await _call(
        dispatcher,
        "lead.list",
        {"status": "paused"},
        request_id=3,
    )
    assert "result" in response
    agents = response["result"]["agents"]
    assert all(a["status"] == "paused" for a in agents)
    assert any(a["agentId"] == lead_b_id for a in agents)


async def test_lead_pause_resume_archive_round_trip(tmp_path: Path) -> None:
    dispatcher, _lifecycle, projects = await _setup(tmp_path)
    project = await _seed_project(projects, slug="alpha")
    spawn = await _call(dispatcher, "lead.spawn", {"projectId": project.project_id})
    agent_id = spawn["result"]["agent"]["agentId"]

    paused = await _call(
        dispatcher,
        "lead.pause",
        {"agentId": agent_id},
        request_id=2,
    )
    assert paused["result"]["agent"]["status"] == "paused"

    resumed = await _call(
        dispatcher,
        "lead.resume",
        {"agentId": agent_id},
        request_id=3,
    )
    assert resumed["result"]["agent"]["status"] == "active"

    archived = await _call(
        dispatcher,
        "lead.archive",
        {"agentId": agent_id},
        request_id=4,
    )
    assert archived["result"]["agent"]["status"] == "archived"

    # Project's lead pointer cleared on archive.
    refetched = await projects.get(project.project_id)
    assert refetched is not None
    assert refetched.lead_agent_id is None


async def test_lead_pause_unknown_agent_returns_invalid_params(tmp_path: Path) -> None:
    dispatcher, _lifecycle, _projects = await _setup(tmp_path)
    response = await _call(
        dispatcher,
        "lead.pause",
        {"agentId": "agent_does_not_exist"},
    )
    assert "error" in response
    assert response["error"]["code"] == INVALID_PARAMS


async def test_lead_pause_on_brain_returns_invalid_params(tmp_path: Path) -> None:
    dispatcher, _lifecycle, _projects = await _setup(tmp_path)
    # The seeded brain agent is not a lifecycle-managed kind.
    response = await _call(
        dispatcher,
        "lead.pause",
        {"agentId": "agent_brain"},
    )
    assert "error" in response
    assert response["error"]["code"] == INVALID_PARAMS
    assert "lifecycle" in response["error"]["message"]


async def test_lead_spawn_sub_lead_returns_wire_record(tmp_path: Path) -> None:
    dispatcher, _lifecycle, projects = await _setup(tmp_path)
    project = await _seed_project(projects, slug="alpha")
    spawn = await _call(dispatcher, "lead.spawn", {"projectId": project.project_id})
    parent_id = spawn["result"]["agent"]["agentId"]

    response = await _call(
        dispatcher,
        "lead.spawn_sub_lead",
        {"parentAgentId": parent_id, "scopeFacet": "ui"},
        request_id=2,
    )

    assert "result" in response, response
    sub = response["result"]["agent"]
    assert sub["kind"] == "sub_lead"
    assert sub["parentAgentId"] == parent_id
    assert sub["scopeFacet"] == "ui"
    assert sub["status"] == "active"


async def test_lead_spawn_sub_lead_depth_cap_returns_structured_error(
    tmp_path: Path,
) -> None:
    dispatcher, _lifecycle, projects = await _setup(tmp_path)
    project = await _seed_project(projects, slug="alpha")
    spawn = await _call(dispatcher, "lead.spawn", {"projectId": project.project_id})
    parent_id = spawn["result"]["agent"]["agentId"]
    sub = await _call(
        dispatcher,
        "lead.spawn_sub_lead",
        {"parentAgentId": parent_id, "scopeFacet": "ui"},
        request_id=2,
    )
    sub_id = sub["result"]["agent"]["agentId"]

    response = await _call(
        dispatcher,
        "lead.spawn_sub_lead",
        {"parentAgentId": sub_id, "scopeFacet": "deeper"},
        request_id=3,
    )

    assert "error" in response, response
    assert response["error"]["code"] == DEPTH_CAP_EXCEEDED
    data = response["error"]["data"]
    assert data["parentAgentId"] == sub_id
    assert data["attemptedDepth"] == 3
    assert data["depthCap"] == 2


async def test_lead_spawn_sub_lead_override_depth_cap_succeeds(
    tmp_path: Path,
) -> None:
    dispatcher, _lifecycle, projects = await _setup(tmp_path)
    project = await _seed_project(projects, slug="alpha")
    spawn = await _call(dispatcher, "lead.spawn", {"projectId": project.project_id})
    parent_id = spawn["result"]["agent"]["agentId"]
    sub = await _call(
        dispatcher,
        "lead.spawn_sub_lead",
        {"parentAgentId": parent_id, "scopeFacet": "ui"},
        request_id=2,
    )
    sub_id = sub["result"]["agent"]["agentId"]

    response = await _call(
        dispatcher,
        "lead.spawn_sub_lead",
        {
            "parentAgentId": sub_id,
            "scopeFacet": "bench",
            "overrideDepthCap": True,
        },
        request_id=3,
    )

    assert "result" in response, response
    deeper = response["result"]["agent"]
    assert deeper["parentAgentId"] == sub_id


async def test_lead_spawn_sub_lead_missing_facet_returns_invalid_params(
    tmp_path: Path,
) -> None:
    dispatcher, _lifecycle, projects = await _setup(tmp_path)
    project = await _seed_project(projects, slug="alpha")
    spawn = await _call(dispatcher, "lead.spawn", {"projectId": project.project_id})
    parent_id = spawn["result"]["agent"]["agentId"]

    response = await _call(
        dispatcher,
        "lead.spawn_sub_lead",
        {"parentAgentId": parent_id},
        request_id=2,
    )

    assert "error" in response
    assert response["error"]["code"] == INVALID_PARAMS
