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
from thalyn_brain.agents import AgentRecord, AgentRecordsStore
from thalyn_brain.lead_lifecycle import (
    DEPTH_CAP,
    DepthCapExceededError,
    LeadLifecycle,
    LeadLifecycleError,
    SpawnRequest,
    SubLeadSpawnRequest,
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
    provider_config: dict[str, object] | None = None,
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


# -----------------------------------------------------------------
# Sub-lead spawn (F2.3 / Phase v0.36)
# -----------------------------------------------------------------


async def _spawn_top_level(
    lifecycle: LeadLifecycle,
    projects: ProjectsStore,
    *,
    name: str = "Alpha",
    slug: str = "alpha",
) -> tuple[Project, AgentRecord]:
    project = await _seed_project(projects, name=name, slug=slug)
    lead = await lifecycle.spawn(SpawnRequest(project_id=project.project_id))
    return project, lead


async def test_spawn_sub_lead_creates_active_record_under_parent(tmp_path: Path) -> None:
    lifecycle, projects, _agents = await _make_lifecycle(tmp_path)
    project, lead = await _spawn_top_level(lifecycle, projects)

    sub = await lifecycle.spawn_sub_lead(
        SubLeadSpawnRequest(parent_agent_id=lead.agent_id, scope_facet="ui"),
    )

    assert sub.kind == "sub_lead"
    assert sub.parent_agent_id == lead.agent_id
    assert sub.project_id == project.project_id
    assert sub.scope_facet == "ui"
    assert sub.status == "active"
    assert sub.display_name == "SubLead-Ui"
    # Namespace nests under the parent's so direct-DB queries respect
    # isolation by construction (F2.3 / project_agent_hierarchy memo).
    assert sub.memory_namespace == f"{lead.memory_namespace}/ui"
    # Provider falls back to the parent's when the request omits one.
    assert sub.default_provider_id == lead.default_provider_id


async def test_spawn_sub_lead_uses_caller_overrides(tmp_path: Path) -> None:
    lifecycle, projects, _agents = await _make_lifecycle(tmp_path)
    _project, lead = await _spawn_top_level(lifecycle, projects)

    sub = await lifecycle.spawn_sub_lead(
        SubLeadSpawnRequest(
            parent_agent_id=lead.agent_id,
            scope_facet="harness",
            display_name="SubLead-Harness-Custom",
            default_provider_id="ollama",
            system_prompt="You audit the harness.",
        ),
    )

    assert sub.display_name == "SubLead-Harness-Custom"
    assert sub.default_provider_id == "ollama"
    assert sub.system_prompt == "You audit the harness."


async def test_spawn_sub_lead_slugifies_multiword_facet(tmp_path: Path) -> None:
    lifecycle, projects, _agents = await _make_lifecycle(tmp_path)
    _project, lead = await _spawn_top_level(lifecycle, projects)

    sub = await lifecycle.spawn_sub_lead(
        SubLeadSpawnRequest(
            parent_agent_id=lead.agent_id,
            scope_facet="cost monitoring",
        ),
    )

    assert sub.scope_facet == "cost monitoring"
    assert sub.display_name == "SubLead-Cost-Monitoring"
    assert sub.memory_namespace.endswith("/cost-monitoring")


async def test_spawn_sub_lead_rejects_blank_facet(tmp_path: Path) -> None:
    lifecycle, projects, _agents = await _make_lifecycle(tmp_path)
    _project, lead = await _spawn_top_level(lifecycle, projects)

    with pytest.raises(LeadLifecycleError, match="scope_facet"):
        await lifecycle.spawn_sub_lead(
            SubLeadSpawnRequest(parent_agent_id=lead.agent_id, scope_facet="   "),
        )


async def test_spawn_sub_lead_rejects_unknown_parent(tmp_path: Path) -> None:
    lifecycle, _projects, _agents = await _make_lifecycle(tmp_path)
    with pytest.raises(LeadLifecycleError, match="does not exist"):
        await lifecycle.spawn_sub_lead(
            SubLeadSpawnRequest(parent_agent_id="agent_missing", scope_facet="ui"),
        )


async def test_spawn_sub_lead_refuses_paused_parent(tmp_path: Path) -> None:
    lifecycle, projects, _agents = await _make_lifecycle(tmp_path)
    _project, lead = await _spawn_top_level(lifecycle, projects)
    await lifecycle.pause(lead.agent_id)

    with pytest.raises(LeadLifecycleError, match="active parent"):
        await lifecycle.spawn_sub_lead(
            SubLeadSpawnRequest(parent_agent_id=lead.agent_id, scope_facet="ui"),
        )


async def test_spawn_sub_lead_refuses_brain_parent(tmp_path: Path) -> None:
    lifecycle, _projects, _agents = await _make_lifecycle(tmp_path)
    # The seeded brain (agent_id='agent_brain') is not lifecycle-managed.
    with pytest.raises(LeadLifecycleError, match="leads or sub-leads"):
        await lifecycle.spawn_sub_lead(
            SubLeadSpawnRequest(parent_agent_id="agent_brain", scope_facet="ui"),
        )


async def test_spawn_sub_lead_enforces_depth_cap(tmp_path: Path) -> None:
    lifecycle, projects, _agents = await _make_lifecycle(tmp_path)
    _project, lead = await _spawn_top_level(lifecycle, projects)
    sub = await lifecycle.spawn_sub_lead(
        SubLeadSpawnRequest(parent_agent_id=lead.agent_id, scope_facet="ui"),
    )

    # Attempting to spawn a sub-lead under another sub-lead lands at
    # depth 3, which exceeds the v1 cap.
    with pytest.raises(DepthCapExceededError) as excinfo:
        await lifecycle.spawn_sub_lead(
            SubLeadSpawnRequest(parent_agent_id=sub.agent_id, scope_facet="extra"),
        )
    assert excinfo.value.attempted_depth == DEPTH_CAP + 1
    assert excinfo.value.parent_agent_id == sub.agent_id


async def test_spawn_sub_lead_allows_explicit_depth_override(tmp_path: Path) -> None:
    lifecycle, projects, _agents = await _make_lifecycle(tmp_path)
    _project, lead = await _spawn_top_level(lifecycle, projects)
    sub = await lifecycle.spawn_sub_lead(
        SubLeadSpawnRequest(parent_agent_id=lead.agent_id, scope_facet="ui"),
    )

    deeper = await lifecycle.spawn_sub_lead(
        SubLeadSpawnRequest(
            parent_agent_id=sub.agent_id,
            scope_facet="bench",
            override_depth_cap=True,
        ),
    )

    assert deeper.parent_agent_id == sub.agent_id
    assert deeper.kind == "sub_lead"
    # Namespace continues to nest so isolation invariants hold even
    # past the cap.
    assert deeper.memory_namespace == f"{sub.memory_namespace}/bench"


async def test_spawn_sub_lead_does_not_replace_project_lead_pointer(tmp_path: Path) -> None:
    lifecycle, projects, _agents = await _make_lifecycle(tmp_path)
    project, lead = await _spawn_top_level(lifecycle, projects)
    await lifecycle.spawn_sub_lead(
        SubLeadSpawnRequest(parent_agent_id=lead.agent_id, scope_facet="ui"),
    )

    refetched = await projects.get(project.project_id)
    assert refetched is not None
    # The project's pointer still references the top-level lead — sub-leads
    # never replace the project's lead pointer (F3.1 invariant).
    assert refetched.lead_agent_id == lead.agent_id


async def test_list_leads_includes_sub_leads_by_default(tmp_path: Path) -> None:
    lifecycle, projects, _agents = await _make_lifecycle(tmp_path)
    project, lead = await _spawn_top_level(lifecycle, projects)
    sub = await lifecycle.spawn_sub_lead(
        SubLeadSpawnRequest(parent_agent_id=lead.agent_id, scope_facet="ui"),
    )

    leads = await lifecycle.list_leads(project_id=project.project_id)
    ids = {r.agent_id for r in leads}
    assert lead.agent_id in ids
    assert sub.agent_id in ids


async def test_pause_resume_round_trip_for_sub_lead(tmp_path: Path) -> None:
    lifecycle, projects, _agents = await _make_lifecycle(tmp_path)
    _project, lead = await _spawn_top_level(lifecycle, projects)
    sub = await lifecycle.spawn_sub_lead(
        SubLeadSpawnRequest(parent_agent_id=lead.agent_id, scope_facet="ui"),
    )

    paused = await lifecycle.pause(sub.agent_id)
    assert paused.status == "paused"
    resumed = await lifecycle.resume(sub.agent_id)
    assert resumed.status == "active"


async def test_archive_sub_lead_does_not_clear_project_lead_pointer(tmp_path: Path) -> None:
    lifecycle, projects, _agents = await _make_lifecycle(tmp_path)
    project, lead = await _spawn_top_level(lifecycle, projects)
    sub = await lifecycle.spawn_sub_lead(
        SubLeadSpawnRequest(parent_agent_id=lead.agent_id, scope_facet="ui"),
    )

    await lifecycle.archive(sub.agent_id)

    refetched = await projects.get(project.project_id)
    assert refetched is not None
    # Top-level lead pointer survives sub-lead archive — only archive
    # of the lead itself unlinks the project.
    assert refetched.lead_agent_id == lead.agent_id
