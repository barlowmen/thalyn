"""Tests for the lead lifecycle state machine.

Covers spawn / pause / resume / archive transitions plus the
``list_leads`` filter and the invariants the state machine enforces:
one active-or-paused lead per project, lifecycle methods refused on
non-lead kinds, archive clearing the project's lead pointer.

The seeded default project + default lead from migration 004 are
treated as test fixtures — most cases create a fresh project so the
state machine's behaviour is exercised against deterministic state.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from thalyn_brain.agents import AgentRecordsStore
from thalyn_brain.lead_lifecycle import (
    LeadLifecycle,
    LeadLifecycleError,
    SpawnRequest,
)
from thalyn_brain.projects import Project, ProjectsStore, new_project_id


def _now_ms() -> int:
    return int(time.time() * 1000)


async def _make_lifecycle(tmp_path: Path) -> tuple[LeadLifecycle, ProjectsStore, AgentRecordsStore]:
    agents = AgentRecordsStore(data_dir=tmp_path)
    projects = ProjectsStore(data_dir=tmp_path)
    lifecycle = LeadLifecycle(agents=agents, projects=projects)
    return lifecycle, projects, agents


async def _seed_project(
    projects: ProjectsStore,
    *,
    name: str = "Alpha",
    slug: str = "alpha",
    provider_config: dict | None = None,
) -> Project:
    project = Project(
        project_id=new_project_id(),
        name=name,
        slug=slug,
        workspace_path=None,
        repo_remote=None,
        lead_agent_id=None,
        memory_namespace=slug,
        conversation_tag=name,
        roadmap="",
        provider_config=provider_config,
        connector_grants=None,
        local_only=False,
        status="active",
        created_at_ms=_now_ms(),
        last_active_at_ms=_now_ms(),
    )
    await projects.insert(project)
    return project


async def test_spawn_creates_active_lead_and_links_project(tmp_path: Path) -> None:
    lifecycle, projects, _agents = await _make_lifecycle(tmp_path)
    project = await _seed_project(projects)

    record = await lifecycle.spawn(SpawnRequest(project_id=project.project_id))

    assert record.kind == "lead"
    assert record.parent_agent_id is None
    assert record.project_id == project.project_id
    assert record.status == "active"
    assert record.display_name == "Lead-Alpha"
    assert record.memory_namespace == "lead-alpha"
    assert record.default_provider_id == "anthropic"

    # The project's lead pointer flips to the new agent.
    refetched = await projects.get(project.project_id)
    assert refetched is not None
    assert refetched.lead_agent_id == record.agent_id


async def test_spawn_uses_caller_overrides_when_provided(tmp_path: Path) -> None:
    lifecycle, projects, _agents = await _make_lifecycle(tmp_path)
    project = await _seed_project(projects, name="Beta", slug="beta")

    record = await lifecycle.spawn(
        SpawnRequest(
            project_id=project.project_id,
            display_name="Sam",
            default_provider_id="ollama",
            system_prompt="You are Sam.",
        )
    )

    assert record.display_name == "Sam"
    assert record.default_provider_id == "ollama"
    assert record.system_prompt == "You are Sam."


async def test_spawn_picks_provider_from_project_config(tmp_path: Path) -> None:
    lifecycle, projects, _agents = await _make_lifecycle(tmp_path)
    project = await _seed_project(
        projects,
        name="Gamma",
        slug="gamma",
        provider_config={"providerId": "anthropic_api"},
    )

    record = await lifecycle.spawn(SpawnRequest(project_id=project.project_id))

    assert record.default_provider_id == "anthropic_api"


async def test_spawn_refuses_when_active_lead_already_exists(tmp_path: Path) -> None:
    lifecycle, projects, _agents = await _make_lifecycle(tmp_path)
    project = await _seed_project(projects)
    await lifecycle.spawn(SpawnRequest(project_id=project.project_id))

    with pytest.raises(LeadLifecycleError, match="already has"):
        await lifecycle.spawn(SpawnRequest(project_id=project.project_id))


async def test_spawn_refuses_when_paused_lead_exists(tmp_path: Path) -> None:
    lifecycle, projects, _agents = await _make_lifecycle(tmp_path)
    project = await _seed_project(projects)
    first = await lifecycle.spawn(SpawnRequest(project_id=project.project_id))
    await lifecycle.pause(first.agent_id)

    with pytest.raises(LeadLifecycleError, match="already has"):
        await lifecycle.spawn(SpawnRequest(project_id=project.project_id))


async def test_spawn_allowed_after_archive(tmp_path: Path) -> None:
    lifecycle, projects, _agents = await _make_lifecycle(tmp_path)
    project = await _seed_project(projects)
    first = await lifecycle.spawn(SpawnRequest(project_id=project.project_id))
    await lifecycle.archive(first.agent_id)

    # Archived doesn't block fresh spawn — the user has retired the
    # prior lead and asked for a new one.
    second = await lifecycle.spawn(SpawnRequest(project_id=project.project_id))
    assert second.agent_id != first.agent_id
    assert second.status == "active"

    refetched = await projects.get(project.project_id)
    assert refetched is not None
    assert refetched.lead_agent_id == second.agent_id


async def test_spawn_rejects_unknown_project(tmp_path: Path) -> None:
    lifecycle, _projects, _agents = await _make_lifecycle(tmp_path)
    with pytest.raises(LeadLifecycleError, match="does not exist"):
        await lifecycle.spawn(SpawnRequest(project_id="proj_does_not_exist"))


async def test_pause_resume_round_trip(tmp_path: Path) -> None:
    lifecycle, projects, _agents = await _make_lifecycle(tmp_path)
    project = await _seed_project(projects)
    record = await lifecycle.spawn(SpawnRequest(project_id=project.project_id))

    paused = await lifecycle.pause(record.agent_id)
    assert paused.status == "paused"

    resumed = await lifecycle.resume(record.agent_id)
    assert resumed.status == "active"


async def test_pause_rejects_non_active(tmp_path: Path) -> None:
    lifecycle, projects, _agents = await _make_lifecycle(tmp_path)
    project = await _seed_project(projects)
    record = await lifecycle.spawn(SpawnRequest(project_id=project.project_id))
    await lifecycle.pause(record.agent_id)

    with pytest.raises(LeadLifecycleError, match="cannot transition"):
        await lifecycle.pause(record.agent_id)


async def test_resume_rejects_non_paused(tmp_path: Path) -> None:
    lifecycle, projects, _agents = await _make_lifecycle(tmp_path)
    project = await _seed_project(projects)
    record = await lifecycle.spawn(SpawnRequest(project_id=project.project_id))

    with pytest.raises(LeadLifecycleError, match="cannot transition"):
        await lifecycle.resume(record.agent_id)


async def test_archive_clears_project_lead_pointer(tmp_path: Path) -> None:
    lifecycle, projects, _agents = await _make_lifecycle(tmp_path)
    project = await _seed_project(projects)
    record = await lifecycle.spawn(SpawnRequest(project_id=project.project_id))

    archived = await lifecycle.archive(record.agent_id)
    assert archived.status == "archived"

    refetched = await projects.get(project.project_id)
    assert refetched is not None
    assert refetched.lead_agent_id is None


async def test_archive_rejects_already_archived(tmp_path: Path) -> None:
    lifecycle, projects, _agents = await _make_lifecycle(tmp_path)
    project = await _seed_project(projects)
    record = await lifecycle.spawn(SpawnRequest(project_id=project.project_id))
    await lifecycle.archive(record.agent_id)

    with pytest.raises(LeadLifecycleError, match="cannot transition"):
        await lifecycle.archive(record.agent_id)


async def test_lifecycle_refuses_brain_kind(tmp_path: Path) -> None:
    lifecycle, _projects, _agents = await _make_lifecycle(tmp_path)
    # The seeded brain (agent_id='agent_brain') exists from migration
    # 004; lifecycle methods aren't defined on it.
    with pytest.raises(LeadLifecycleError, match="lifecycle is"):
        await lifecycle.pause("agent_brain")


async def test_list_leads_default_kind_filter(tmp_path: Path) -> None:
    lifecycle, projects, _agents = await _make_lifecycle(tmp_path)
    project = await _seed_project(projects)
    record = await lifecycle.spawn(SpawnRequest(project_id=project.project_id))

    leads = await lifecycle.list_leads(project_id=project.project_id)
    assert any(r.agent_id == record.agent_id for r in leads)
    assert all(r.kind in {"lead", "sub_lead"} for r in leads)


async def test_list_leads_status_filter(tmp_path: Path) -> None:
    lifecycle, projects, _agents = await _make_lifecycle(tmp_path)
    project_a = await _seed_project(projects, name="A", slug="proj-a")
    project_b = await _seed_project(projects, name="B", slug="proj-b")
    lead_a = await lifecycle.spawn(SpawnRequest(project_id=project_a.project_id))
    lead_b = await lifecycle.spawn(SpawnRequest(project_id=project_b.project_id))
    await lifecycle.pause(lead_b.agent_id)

    actives = await lifecycle.list_leads(status="active")
    active_ids = {r.agent_id for r in actives}
    assert lead_a.agent_id in active_ids
    assert lead_b.agent_id not in active_ids

    paused = await lifecycle.list_leads(status="paused")
    paused_ids = {r.agent_id for r in paused}
    assert lead_b.agent_id in paused_ids
    assert lead_a.agent_id not in paused_ids


async def test_list_leads_rejects_invalid_status(tmp_path: Path) -> None:
    lifecycle, _projects, _agents = await _make_lifecycle(tmp_path)
    with pytest.raises(LeadLifecycleError):
        await lifecycle.list_leads(status="invalid")


async def test_list_leads_rejects_non_lifecycle_kind(tmp_path: Path) -> None:
    lifecycle, _projects, _agents = await _make_lifecycle(tmp_path)
    with pytest.raises(LeadLifecycleError):
        await lifecycle.list_leads(kind="brain")
