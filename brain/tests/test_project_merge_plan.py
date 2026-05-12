"""Tests for the project-merge planner.

The planner is pure-read — every call into the stores is a query, no
mutation — so these tests build two synthetic projects, seed turns /
memory / routing rows / connector grants, and assert the plan
captures exactly what the apply step would change. The apply step
itself lives in a sibling test module so the responsibilities stay
crisp.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from thalyn_brain.agents import AgentRecord, AgentRecordsStore, new_agent_id
from thalyn_brain.memory import MemoryEntry, MemoryStore, new_memory_id
from thalyn_brain.project_merge import (
    ProjectMergeError,
    compute_merge_plan,
)
from thalyn_brain.projects import Project, ProjectsStore, new_project_id
from thalyn_brain.routing import (
    RoutingOverride,
    RoutingOverridesStore,
    new_routing_override_id,
)
from thalyn_brain.threads import (
    Thread,
    ThreadsStore,
    ThreadTurn,
    new_thread_id,
    new_turn_id,
)


def _now_ms() -> int:
    return int(time.time() * 1000)


async def _make_stores(
    tmp_path: Path,
) -> tuple[
    ProjectsStore,
    ThreadsStore,
    MemoryStore,
    AgentRecordsStore,
    RoutingOverridesStore,
]:
    return (
        ProjectsStore(data_dir=tmp_path),
        ThreadsStore(data_dir=tmp_path),
        MemoryStore(data_dir=tmp_path),
        AgentRecordsStore(data_dir=tmp_path),
        RoutingOverridesStore(data_dir=tmp_path),
    )


async def _seed_project(
    projects: ProjectsStore,
    *,
    name: str,
    slug: str,
    connector_grants: dict[str, object] | None = None,
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
        provider_config=None,
        connector_grants=connector_grants,
        local_only=False,
        status="active",
        created_at_ms=_now_ms(),
        last_active_at_ms=_now_ms(),
    )
    await projects.insert(project)
    return project


async def _seed_lead(
    agents: AgentRecordsStore,
    projects: ProjectsStore,
    project: Project,
    *,
    parent_agent_id: str | None = None,
    kind: str = "lead",
) -> AgentRecord:
    record = AgentRecord(
        agent_id=new_agent_id(),
        kind=kind,
        display_name=f"Lead-{project.name}" if kind == "lead" else f"Sub-{project.name}",
        parent_agent_id=parent_agent_id,
        project_id=project.project_id,
        scope_facet=None,
        memory_namespace=f"lead-{project.slug}",
        default_provider_id="anthropic",
        system_prompt="",
        status="active",
        created_at_ms=_now_ms(),
        last_active_at_ms=_now_ms(),
    )
    await agents.insert(record)
    if kind == "lead" and parent_agent_id is None:
        await projects.set_lead(project.project_id, record.agent_id)
    return record


async def _seed_thread(threads: ThreadsStore) -> Thread:
    thread = Thread(
        thread_id=new_thread_id(),
        user_scope="self",
        created_at_ms=_now_ms(),
        last_active_at_ms=_now_ms(),
    )
    await threads.insert_thread(thread)
    return thread


async def _seed_turn(
    threads: ThreadsStore,
    *,
    thread: Thread,
    project: Project | None,
    body: str,
    role: str = "user",
) -> ThreadTurn:
    turn = ThreadTurn(
        turn_id=new_turn_id(),
        thread_id=thread.thread_id,
        project_id=project.project_id if project else None,
        agent_id=None,
        role=role,
        body=body,
        provenance=None,
        confidence=None,
        episodic_index_ptr=None,
        at_ms=_now_ms(),
    )
    await threads.insert_turn(turn)
    return turn


async def _seed_memory(
    memory: MemoryStore,
    *,
    project: Project | None,
    body: str,
    scope: str = "project",
) -> MemoryEntry:
    entry = MemoryEntry(
        memory_id=new_memory_id(),
        project_id=project.project_id if project else None,
        scope=scope,
        kind="fact",
        body=body,
        author="thalyn",
        created_at_ms=_now_ms(),
        updated_at_ms=_now_ms(),
    )
    await memory.insert(entry)
    return entry


async def _seed_routing(
    routing_overrides: RoutingOverridesStore,
    *,
    project: Project,
    task_tag: str,
    provider_id: str,
) -> RoutingOverride:
    override = RoutingOverride(
        routing_override_id=new_routing_override_id(),
        project_id=project.project_id,
        task_tag=task_tag,
        provider_id=provider_id,
        updated_at_ms=_now_ms(),
    )
    await routing_overrides.upsert(override)
    # The unique constraint on (project_id, task_tag) means upsert
    # returns the canonical row; re-read so the planner sees the
    # stored override id.
    stored = await routing_overrides.get(project.project_id, task_tag)
    assert stored is not None
    return stored


async def test_plan_captures_turns_memory_lead_for_simple_merge(tmp_path: Path) -> None:
    projects, threads, memory, agents, routing = await _make_stores(tmp_path)
    absorbed = await _seed_project(projects, name="UI", slug="ui")
    surviving = await _seed_project(projects, name="Thalyn", slug="thalyn")
    absorbed_lead = await _seed_lead(agents, projects, absorbed)
    surviving_lead = await _seed_lead(agents, projects, surviving)

    thread = await _seed_thread(threads)
    a_turn = await _seed_turn(threads, thread=thread, project=absorbed, body="ui work")
    await _seed_turn(threads, thread=thread, project=surviving, body="thalyn work")
    await _seed_turn(threads, thread=thread, project=None, body="untagged")
    a_memory = await _seed_memory(memory, project=absorbed, body="learned about ui")
    await _seed_memory(memory, project=surviving, body="learned about thalyn")

    plan = await compute_merge_plan(
        from_project_id=absorbed.project_id,
        into_project_id=surviving.project_id,
        projects=projects,
        threads=threads,
        memory=memory,
        agents=agents,
        routing_overrides=routing,
    )

    # Only the absorbed-project rows show up; surviving / untagged rows
    # stay where they are.
    assert plan.thread_turn_ids == (a_turn.turn_id,)
    assert plan.memory_entry_ids == (a_memory.memory_id,)
    assert plan.absorbed_lead is not None
    assert plan.absorbed_lead.agent_id == absorbed_lead.agent_id
    assert plan.surviving_lead is not None
    assert plan.surviving_lead.agent_id == surviving_lead.agent_id
    assert plan.re_parent_sub_lead_ids == ()
    assert plan.routing_overrides_to_migrate == ()
    assert plan.routing_override_conflicts == ()
    assert plan.merged_connector_grants is None
    assert plan.connector_grant_conflicts == ()
    assert plan.merge_id.startswith("merge_")
    assert plan.computed_at_ms > 0


async def test_plan_includes_routing_override_migration_when_no_conflict(
    tmp_path: Path,
) -> None:
    projects, threads, memory, agents, routing = await _make_stores(tmp_path)
    absorbed = await _seed_project(projects, name="UI", slug="ui")
    surviving = await _seed_project(projects, name="Thalyn", slug="thalyn")
    coding_route = await _seed_routing(
        routing,
        project=absorbed,
        task_tag="coding",
        provider_id="ollama",
    )

    plan = await compute_merge_plan(
        from_project_id=absorbed.project_id,
        into_project_id=surviving.project_id,
        projects=projects,
        threads=threads,
        memory=memory,
        agents=agents,
        routing_overrides=routing,
    )

    assert len(plan.routing_overrides_to_migrate) == 1
    migration = plan.routing_overrides_to_migrate[0]
    assert migration.task_tag == "coding"
    assert migration.provider_id == "ollama"
    assert migration.routing_override_id == coding_route.routing_override_id
    assert plan.routing_override_conflicts == ()


async def test_plan_records_routing_override_conflict_surviving_wins(
    tmp_path: Path,
) -> None:
    projects, threads, memory, agents, routing = await _make_stores(tmp_path)
    absorbed = await _seed_project(projects, name="UI", slug="ui")
    surviving = await _seed_project(projects, name="Thalyn", slug="thalyn")
    absorbed_coding = await _seed_routing(
        routing,
        project=absorbed,
        task_tag="coding",
        provider_id="ollama",
    )
    await _seed_routing(
        routing,
        project=surviving,
        task_tag="coding",
        provider_id="anthropic",
    )

    plan = await compute_merge_plan(
        from_project_id=absorbed.project_id,
        into_project_id=surviving.project_id,
        projects=projects,
        threads=threads,
        memory=memory,
        agents=agents,
        routing_overrides=routing,
    )

    assert plan.routing_overrides_to_migrate == ()
    assert len(plan.routing_override_conflicts) == 1
    conflict = plan.routing_override_conflicts[0]
    assert conflict.task_tag == "coding"
    assert conflict.surviving_provider_id == "anthropic"
    assert conflict.absorbed_provider_id == "ollama"
    assert conflict.absorbed_routing_override_id == absorbed_coding.routing_override_id


async def test_plan_unions_connector_grants_with_conflict_recorded(
    tmp_path: Path,
) -> None:
    projects, threads, memory, agents, routing = await _make_stores(tmp_path)
    absorbed = await _seed_project(
        projects,
        name="UI",
        slug="ui",
        connector_grants={"slack": "channel:ui", "linear": "team:ui"},
    )
    surviving = await _seed_project(
        projects,
        name="Thalyn",
        slug="thalyn",
        connector_grants={"slack": "channel:thalyn"},
    )

    plan = await compute_merge_plan(
        from_project_id=absorbed.project_id,
        into_project_id=surviving.project_id,
        projects=projects,
        threads=threads,
        memory=memory,
        agents=agents,
        routing_overrides=routing,
    )

    assert plan.merged_connector_grants == {
        "slack": "channel:thalyn",  # surviving wins
        "linear": "team:ui",  # absorbed-only key migrates
    }
    assert len(plan.connector_grant_conflicts) == 1
    conflict = plan.connector_grant_conflicts[0]
    assert conflict.key == "slack"
    assert conflict.surviving_value == "channel:thalyn"
    assert conflict.absorbed_value == "channel:ui"


async def test_plan_returns_empty_when_both_projects_have_no_grants(
    tmp_path: Path,
) -> None:
    projects, threads, memory, agents, routing = await _make_stores(tmp_path)
    absorbed = await _seed_project(projects, name="UI", slug="ui")
    surviving = await _seed_project(projects, name="Thalyn", slug="thalyn")

    plan = await compute_merge_plan(
        from_project_id=absorbed.project_id,
        into_project_id=surviving.project_id,
        projects=projects,
        threads=threads,
        memory=memory,
        agents=agents,
        routing_overrides=routing,
    )

    assert plan.merged_connector_grants is None
    assert plan.connector_grant_conflicts == ()


async def test_plan_lists_sub_leads_parented_to_absorbed_lead(tmp_path: Path) -> None:
    projects, threads, memory, agents, routing = await _make_stores(tmp_path)
    absorbed = await _seed_project(projects, name="UI", slug="ui")
    surviving = await _seed_project(projects, name="Thalyn", slug="thalyn")
    absorbed_lead = await _seed_lead(agents, projects, absorbed)
    await _seed_lead(agents, projects, surviving)
    sub_lead = await _seed_lead(
        agents,
        projects,
        absorbed,
        parent_agent_id=absorbed_lead.agent_id,
        kind="sub_lead",
    )

    plan = await compute_merge_plan(
        from_project_id=absorbed.project_id,
        into_project_id=surviving.project_id,
        projects=projects,
        threads=threads,
        memory=memory,
        agents=agents,
        routing_overrides=routing,
    )

    assert plan.re_parent_sub_lead_ids == (sub_lead.agent_id,)


async def test_plan_to_wire_round_trips_counts(tmp_path: Path) -> None:
    projects, threads, memory, agents, routing = await _make_stores(tmp_path)
    absorbed = await _seed_project(projects, name="UI", slug="ui")
    surviving = await _seed_project(projects, name="Thalyn", slug="thalyn")
    thread = await _seed_thread(threads)
    await _seed_turn(threads, thread=thread, project=absorbed, body="ui")
    await _seed_memory(memory, project=absorbed, body="fact")

    plan = await compute_merge_plan(
        from_project_id=absorbed.project_id,
        into_project_id=surviving.project_id,
        projects=projects,
        threads=threads,
        memory=memory,
        agents=agents,
        routing_overrides=routing,
    )
    wire = plan.to_wire()
    assert wire["counts"]["threadTurns"] == 1
    assert wire["counts"]["memoryEntries"] == 1
    assert wire["counts"]["routingMigrations"] == 0
    assert wire["counts"]["routingConflicts"] == 0
    assert wire["counts"]["subLeadReParents"] == 0
    assert wire["fromProject"]["projectId"] == absorbed.project_id
    assert wire["intoProject"]["projectId"] == surviving.project_id


async def test_plan_refuses_self_merge(tmp_path: Path) -> None:
    projects, threads, memory, agents, routing = await _make_stores(tmp_path)
    project = await _seed_project(projects, name="UI", slug="ui")
    with pytest.raises(ProjectMergeError, match="into itself"):
        await compute_merge_plan(
            from_project_id=project.project_id,
            into_project_id=project.project_id,
            projects=projects,
            threads=threads,
            memory=memory,
            agents=agents,
            routing_overrides=routing,
        )


async def test_plan_refuses_archived_surviving(tmp_path: Path) -> None:
    projects, threads, memory, agents, routing = await _make_stores(tmp_path)
    absorbed = await _seed_project(projects, name="UI", slug="ui")
    surviving = await _seed_project(projects, name="Thalyn", slug="thalyn")
    await projects.set_status(surviving.project_id, "archived")
    with pytest.raises(ProjectMergeError, match="archived"):
        await compute_merge_plan(
            from_project_id=absorbed.project_id,
            into_project_id=surviving.project_id,
            projects=projects,
            threads=threads,
            memory=memory,
            agents=agents,
            routing_overrides=routing,
        )


async def test_plan_refuses_unknown_project(tmp_path: Path) -> None:
    projects, threads, memory, agents, routing = await _make_stores(tmp_path)
    surviving = await _seed_project(projects, name="Thalyn", slug="thalyn")
    with pytest.raises(ProjectMergeError, match="does not exist"):
        await compute_merge_plan(
            from_project_id="proj_nonexistent",
            into_project_id=surviving.project_id,
            projects=projects,
            threads=threads,
            memory=memory,
            agents=agents,
            routing_overrides=routing,
        )
