"""Tests for the ``project.*`` JSON-RPC surface (v0.31).

The handlers are thin wrappers over ``ProjectsStore`` plus the
``LeadLifecycle`` archive cascade. The state-machine semantics
themselves live in ``test_projects`` and ``test_lead_lifecycle``;
this surface test asserts each method (a) parses params, (b)
translates store errors into ``INVALID_PARAMS``, and (c) returns
the wire-shape project record.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from thalyn_brain.agents import AgentRecordsStore
from thalyn_brain.lead_lifecycle import LeadLifecycle, SpawnRequest
from thalyn_brain.project_rpc import register_project_methods
from thalyn_brain.projects import ProjectsStore
from thalyn_brain.rpc import INVALID_PARAMS, Dispatcher


class _DropNotify:
    async def __call__(self, method: str, params: Any) -> None:
        return None


_drop_notify = _DropNotify()


async def _setup(tmp_path: Path) -> tuple[Dispatcher, ProjectsStore, LeadLifecycle]:
    projects = ProjectsStore(data_dir=tmp_path)
    agents = AgentRecordsStore(data_dir=tmp_path)
    lifecycle = LeadLifecycle(agents=agents, projects=projects)
    dispatcher = Dispatcher()
    register_project_methods(dispatcher, projects=projects, lead_lifecycle=lifecycle)
    return dispatcher, projects, lifecycle


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


async def test_create_returns_wire_record(tmp_path: Path) -> None:
    dispatcher, projects, _ = await _setup(tmp_path)
    response = await _call(
        dispatcher,
        "project.create",
        {"name": "Tax Prep 2026"},
    )
    assert "result" in response
    project = response["result"]["project"]
    assert project["name"] == "Tax Prep 2026"
    assert project["slug"] == "tax-prep-2026"
    assert project["status"] == "active"
    fetched = await projects.get(project["projectId"])
    assert fetched is not None


async def test_create_rejects_empty_name(tmp_path: Path) -> None:
    dispatcher, _, _ = await _setup(tmp_path)
    response = await _call(dispatcher, "project.create", {"name": "   "})
    assert "error" in response
    assert response["error"]["code"] == INVALID_PARAMS


async def test_list_filters_by_status(tmp_path: Path) -> None:
    dispatcher, projects, _ = await _setup(tmp_path)
    alpha = await projects.create(name="Alpha")
    beta = await projects.create(name="Beta")
    await projects.set_status(beta.project_id, "paused")

    response = await _call(dispatcher, "project.list", {"status": "active"})
    assert "result" in response
    active_ids = {p["projectId"] for p in response["result"]["projects"]}
    assert alpha.project_id in active_ids
    assert beta.project_id not in active_ids


async def test_list_rejects_invalid_status(tmp_path: Path) -> None:
    dispatcher, _, _ = await _setup(tmp_path)
    response = await _call(dispatcher, "project.list", {"status": "rotting"})
    assert "error" in response
    assert response["error"]["code"] == INVALID_PARAMS


async def test_update_renames(tmp_path: Path) -> None:
    dispatcher, projects, _ = await _setup(tmp_path)
    project = await projects.create(name="Original")
    response = await _call(
        dispatcher,
        "project.update",
        {"projectId": project.project_id, "name": "Renamed"},
    )
    assert "result" in response
    assert response["result"]["project"]["name"] == "Renamed"
    refreshed = await projects.get(project.project_id)
    assert refreshed is not None
    assert refreshed.name == "Renamed"


async def test_update_flips_local_only(tmp_path: Path) -> None:
    dispatcher, projects, _ = await _setup(tmp_path)
    project = await projects.create(name="Alpha")
    response = await _call(
        dispatcher,
        "project.update",
        {"projectId": project.project_id, "localOnly": True},
    )
    assert "result" in response
    assert response["result"]["project"]["localOnly"] is True


async def test_update_unknown_project_errors(tmp_path: Path) -> None:
    dispatcher, _, _ = await _setup(tmp_path)
    response = await _call(
        dispatcher,
        "project.update",
        {"projectId": "proj_missing", "name": "anything"},
    )
    assert "error" in response
    assert response["error"]["code"] == INVALID_PARAMS


async def test_pause_resume(tmp_path: Path) -> None:
    dispatcher, projects, _ = await _setup(tmp_path)
    project = await projects.create(name="Alpha")

    paused = await _call(
        dispatcher,
        "project.pause",
        {"projectId": project.project_id},
    )
    assert paused["result"]["project"]["status"] == "paused"

    resumed = await _call(
        dispatcher,
        "project.resume",
        {"projectId": project.project_id},
    )
    assert resumed["result"]["project"]["status"] == "active"


async def test_archive_cascades_through_lead(tmp_path: Path) -> None:
    dispatcher, projects, lifecycle = await _setup(tmp_path)
    project = await projects.create(name="Alpha")
    lead = await lifecycle.spawn(SpawnRequest(project_id=project.project_id))

    response = await _call(
        dispatcher,
        "project.archive",
        {"projectId": project.project_id},
    )
    assert "result" in response
    assert response["result"]["project"]["status"] == "archived"
    assert response["result"]["project"]["leadAgentId"] is None
    archived = await lifecycle.list_leads(project_id=project.project_id, status="archived")
    assert any(record.agent_id == lead.agent_id for record in archived)


async def test_archive_unknown_project_errors(tmp_path: Path) -> None:
    dispatcher, _, _ = await _setup(tmp_path)
    response = await _call(
        dispatcher,
        "project.archive",
        {"projectId": "proj_missing"},
    )
    assert "error" in response
    assert response["error"]["code"] == INVALID_PARAMS
