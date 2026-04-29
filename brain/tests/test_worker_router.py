"""Integration tests for ``StoreBackedWorkerRouter``.

The default ``WorkerRouter`` reads overrides + the project's
``local_only`` flag from SQLite and runs the pure ``route_worker``
function. These tests assert the three resolution branches end-to-end:
no overrides → global default; per-project override; ``local_only``
short-circuit. The belt-and-braces refusal is covered by directly
asserting the ``LocalOnlyViolation`` raise when a non-local provider
is asked to run inside a ``local_only`` project.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from thalyn_brain.projects import Project, ProjectsStore, new_project_id
from thalyn_brain.provider import build_registry
from thalyn_brain.routing import (
    RoutingOverride,
    RoutingOverridesStore,
    new_routing_override_id,
)
from thalyn_brain.routing_table import LocalOnlyViolation, MatchedRule
from thalyn_brain.worker_router import StoreBackedWorkerRouter


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


def _build_router(
    tmp_path: Path,
) -> tuple[
    StoreBackedWorkerRouter,
    ProjectsStore,
    RoutingOverridesStore,
]:
    projects = ProjectsStore(data_dir=tmp_path)
    overrides = RoutingOverridesStore(data_dir=tmp_path)
    registry = build_registry()
    router = StoreBackedWorkerRouter(
        overrides_store=overrides,
        projects_store=projects,
        registry=registry,
    )
    return router, projects, overrides


@pytest.mark.asyncio
async def test_router_resolves_global_default_with_no_overrides(tmp_path: Path) -> None:
    router, projects, _overrides = _build_router(tmp_path)
    project = await _seed_project(projects)

    decision = await router.route(task_tag="coding", project_id=project.project_id)
    assert decision.provider_id == "anthropic"
    assert decision.matched is MatchedRule.GLOBAL


@pytest.mark.asyncio
async def test_router_resolves_per_project_override(tmp_path: Path) -> None:
    router, projects, overrides = _build_router(tmp_path)
    project = await _seed_project(projects)

    await overrides.upsert(
        RoutingOverride(
            routing_override_id=new_routing_override_id(),
            project_id=project.project_id,
            task_tag="coding",
            provider_id="ollama",
            updated_at_ms=int(time.time() * 1000),
        )
    )

    decision = await router.route(task_tag="coding", project_id=project.project_id)
    assert decision.provider_id == "ollama"
    assert decision.matched is MatchedRule.OVERRIDE


@pytest.mark.asyncio
async def test_router_short_circuits_to_local_only(tmp_path: Path) -> None:
    router, projects, overrides = _build_router(tmp_path)
    project = await _seed_project(projects, local_only=True)

    # Even a stale override pointing at a cloud provider is ignored.
    await overrides.upsert(
        RoutingOverride(
            routing_override_id=new_routing_override_id(),
            project_id=project.project_id,
            task_tag="coding",
            provider_id="anthropic",
            updated_at_ms=int(time.time() * 1000),
        )
    )

    decision = await router.route(task_tag="coding", project_id=project.project_id)
    assert decision.matched is MatchedRule.LOCAL_ONLY
    assert decision.provider_id in {"mlx", "ollama"}


@pytest.mark.asyncio
async def test_router_falls_back_to_global_when_project_missing(tmp_path: Path) -> None:
    """A ``project_id`` that doesn't exist in the store must not raise —
    the IPC surface allows previewing routes for a project that hasn't
    been created yet (setup wizard); the spawn path uses the same
    code, so the router degrades to the global default cleanly."""
    router, _projects, _overrides = _build_router(tmp_path)
    decision = await router.route(task_tag="coding", project_id="proj_missing")
    assert decision.matched is MatchedRule.GLOBAL


@pytest.mark.asyncio
async def test_router_no_project_id_uses_global_default(tmp_path: Path) -> None:
    """The brain's own runs (no project lead yet) call the spawn path
    without a project id; the router answers with the global defaults
    rather than treating the missing context as an error."""
    router, _projects, _overrides = _build_router(tmp_path)
    decision = await router.route(task_tag="coding", project_id=None)
    assert decision.matched is MatchedRule.GLOBAL
    assert decision.provider_id == "anthropic"


@pytest.mark.asyncio
async def test_router_belt_and_braces_blocks_cloud_for_local_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The risk pre-flight: confirm a cloud token is *refused* for a
    ``local_only`` project's run even if a stale override / racing
    write somehow nominates one. The route_worker function won't
    pick a cloud provider for ``local_only`` — but if the call site
    bypassed it, the router's capability-profile check is the second
    line of defence."""
    from thalyn_brain import worker_router as wr_module
    from thalyn_brain.routing_table import RouteDecision

    projects = ProjectsStore(data_dir=tmp_path)
    overrides = RoutingOverridesStore(data_dir=tmp_path)
    registry = build_registry()
    project = await _seed_project(projects, local_only=True)
    router = StoreBackedWorkerRouter(
        overrides_store=overrides,
        projects_store=projects,
        registry=registry,
    )

    # Patch the module-level resolver to simulate a bypass — a stale
    # override or a racing write nominating a cloud provider for a
    # ``local_only`` project. The capability-profile assertion in the
    # router is the second line of defence.
    bypassed = RouteDecision(
        provider_id="anthropic",
        task_tag="coding",
        effective_tag="coding",
        matched=MatchedRule.OVERRIDE,
    )
    monkeypatch.setattr(wr_module, "route_worker", lambda **_kw: bypassed)

    with pytest.raises(LocalOnlyViolation):
        await router.route(task_tag="coding", project_id=project.project_id)
