"""Tests for the worker-routing RPC surface.

The handlers wrap ``RoutingOverridesStore`` and the pure
``route_worker`` resolver. The surface tests assert each method
(a) parses params, (b) returns the wire shape, and (c) honours the
``local_only`` short-circuit through ``routing.get``. End-to-end
spawn-time routing integration lives in ``test_runner_routing``.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
from thalyn_brain.projects import Project, ProjectsStore, new_project_id
from thalyn_brain.routing import RoutingOverridesStore
from thalyn_brain.routing_rpc import register_routing_methods
from thalyn_brain.rpc import INVALID_PARAMS, Dispatcher


class _DropNotify:
    async def __call__(self, method: str, params: Any) -> None:
        return None


_drop_notify = _DropNotify()


async def _setup(
    tmp_path: Path,
    *,
    valid_provider_ids: set[str] | None = None,
) -> tuple[Dispatcher, ProjectsStore, RoutingOverridesStore]:
    projects = ProjectsStore(data_dir=tmp_path)
    overrides = RoutingOverridesStore(data_dir=tmp_path)
    dispatcher = Dispatcher()
    register_routing_methods(
        dispatcher,
        overrides_store=overrides,
        projects_store=projects,
        valid_provider_ids=valid_provider_ids,
    )
    return dispatcher, projects, overrides


async def _seed_project(
    projects: ProjectsStore,
    *,
    slug: str = "alpha",
    local_only: bool = False,
) -> Project:
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
        local_only=local_only,
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


@pytest.mark.asyncio
async def test_routing_get_falls_through_to_global_default(tmp_path: Path) -> None:
    dispatcher, projects, _overrides = await _setup(tmp_path)
    project = await _seed_project(projects)

    response = await _call(
        dispatcher,
        "routing.get",
        {"projectId": project.project_id, "taskTag": "coding"},
    )

    result = response["result"]
    assert result["providerId"] == "anthropic"
    assert result["matched"] == "global"
    assert result["effectiveTag"] == "coding"


@pytest.mark.asyncio
async def test_routing_set_then_get_returns_override(tmp_path: Path) -> None:
    dispatcher, projects, _overrides = await _setup(tmp_path)
    project = await _seed_project(projects)

    set_response = await _call(
        dispatcher,
        "routing.set",
        {
            "projectId": project.project_id,
            "taskTag": "coding",
            "providerId": "ollama",
        },
    )
    assert "result" in set_response
    assert set_response["result"]["override"]["providerId"] == "ollama"

    get_response = await _call(
        dispatcher,
        "routing.get",
        {"projectId": project.project_id, "taskTag": "coding"},
        request_id=2,
    )
    assert get_response["result"]["providerId"] == "ollama"
    assert get_response["result"]["matched"] == "override"


@pytest.mark.asyncio
async def test_routing_set_rejects_unknown_provider_when_allowlist_configured(
    tmp_path: Path,
) -> None:
    dispatcher, projects, _overrides = await _setup(
        tmp_path,
        valid_provider_ids={"anthropic", "ollama"},
    )
    project = await _seed_project(projects)

    response = await _call(
        dispatcher,
        "routing.set",
        {
            "projectId": project.project_id,
            "taskTag": "coding",
            "providerId": "made-up",
        },
    )

    assert "error" in response
    assert response["error"]["code"] == INVALID_PARAMS


@pytest.mark.asyncio
async def test_routing_clear_removes_override(tmp_path: Path) -> None:
    dispatcher, projects, _overrides = await _setup(tmp_path)
    project = await _seed_project(projects)

    await _call(
        dispatcher,
        "routing.set",
        {
            "projectId": project.project_id,
            "taskTag": "coding",
            "providerId": "ollama",
        },
    )
    cleared = await _call(
        dispatcher,
        "routing.clear",
        {"projectId": project.project_id, "taskTag": "coding"},
        request_id=2,
    )
    assert cleared["result"]["cleared"] is True

    get_response = await _call(
        dispatcher,
        "routing.get",
        {"projectId": project.project_id, "taskTag": "coding"},
        request_id=3,
    )
    # Falls back to the global default once the override is gone.
    assert get_response["result"]["matched"] == "global"


@pytest.mark.asyncio
async def test_routing_get_short_circuits_to_local_only(tmp_path: Path) -> None:
    dispatcher, projects, _overrides = await _setup(tmp_path)
    project = await _seed_project(projects, local_only=True)

    # Even with an override present, ``local_only`` wins.
    await _call(
        dispatcher,
        "routing.set",
        {
            "projectId": project.project_id,
            "taskTag": "coding",
            "providerId": "anthropic",
        },
    )

    response = await _call(
        dispatcher,
        "routing.get",
        {"projectId": project.project_id, "taskTag": "coding"},
        request_id=2,
    )
    assert response["result"]["matched"] == "local_only"
    assert response["result"]["providerId"] in {"mlx", "ollama"}


@pytest.mark.asyncio
async def test_routing_list_enumerates_project_overrides(tmp_path: Path) -> None:
    dispatcher, projects, _overrides = await _setup(tmp_path)
    project = await _seed_project(projects)

    for tag, provider in (("coding", "ollama"), ("research", "mlx")):
        await _call(
            dispatcher,
            "routing.set",
            {
                "projectId": project.project_id,
                "taskTag": tag,
                "providerId": provider,
            },
        )

    response = await _call(
        dispatcher,
        "routing.list",
        {"projectId": project.project_id},
    )
    overrides = response["result"]["overrides"]
    pairs = {(o["taskTag"], o["providerId"]) for o in overrides}
    assert pairs == {("coding", "ollama"), ("research", "mlx")}


@pytest.mark.asyncio
async def test_routing_get_handles_missing_project(tmp_path: Path) -> None:
    """A project that doesn't exist should still resolve to the global
    default — the IPC surface stays usable for setup-wizard previews
    that haven't created the project yet."""
    dispatcher, _projects, _overrides = await _setup(tmp_path)

    response = await _call(
        dispatcher,
        "routing.get",
        {"projectId": "proj_nonexistent", "taskTag": "coding"},
    )
    assert response["result"]["providerId"] == "anthropic"
    assert response["result"]["matched"] == "global"


@pytest.mark.asyncio
async def test_routing_set_requires_strings(tmp_path: Path) -> None:
    dispatcher, projects, _overrides = await _setup(tmp_path)
    project = await _seed_project(projects)

    response = await _call(
        dispatcher,
        "routing.set",
        {"projectId": project.project_id, "taskTag": "coding"},
    )
    assert "error" in response
    assert response["error"]["code"] == INVALID_PARAMS
