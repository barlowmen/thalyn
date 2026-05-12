"""Tests for ``apply_merge_plan`` — the transactional writer.

The applier consumes a plan (computed by ``compute_merge_plan``) and
applies it in one ``BEGIN IMMEDIATE`` transaction over ``app.db``.
These tests build two synthetic projects with overlapping data, run
the full plan-then-apply path, and assert the database reflects every
F3.4 invariant: turns re-tagged, memory migrated + lead re-anchored,
absorbed lead archived, absorbed project archived, routing overrides
migrated / conflicts dropped, connector-grant blob unioned, audit log
written.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from thalyn_brain.agents import AgentRecord, AgentRecordsStore, new_agent_id
from thalyn_brain.memory import MemoryEntry, MemoryStore, new_memory_id
from thalyn_brain.project_merge import (
    apply_merge_plan,
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
    agent_id: str | None = None,
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
    if agent_id is not None:
        # MemoryStore.insert doesn't carry agent_id (v2 column added
        # in migration 003); set it directly so the re-anchor test
        # has a row to re-anchor.
        import sqlite3

        with sqlite3.connect(memory._db_path) as conn:
            conn.execute(
                "UPDATE memory_entries SET agent_id = ? WHERE memory_id = ?",
                (agent_id, entry.memory_id),
            )
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
    stored = await routing_overrides.get(project.project_id, task_tag)
    assert stored is not None
    return stored


async def test_apply_rewrites_turns_and_migrates_memory(tmp_path: Path) -> None:
    projects, threads, memory, agents, routing = await _make_stores(tmp_path)
    absorbed = await _seed_project(projects, name="UI", slug="ui")
    surviving = await _seed_project(projects, name="Thalyn", slug="thalyn")
    absorbed_lead = await _seed_lead(agents, projects, absorbed)
    surviving_lead = await _seed_lead(agents, projects, surviving)

    thread = await _seed_thread(threads)
    a_turn = await _seed_turn(threads, thread=thread, project=absorbed, body="ui work")
    b_turn = await _seed_turn(threads, thread=thread, project=surviving, body="thalyn work")
    a_memory = await _seed_memory(
        memory,
        project=absorbed,
        body="learned about ui",
        agent_id=absorbed_lead.agent_id,
    )
    b_memory = await _seed_memory(memory, project=surviving, body="learned about thalyn")

    plan = await compute_merge_plan(
        from_project_id=absorbed.project_id,
        into_project_id=surviving.project_id,
        projects=projects,
        threads=threads,
        memory=memory,
        agents=agents,
        routing_overrides=routing,
    )
    outcome = await apply_merge_plan(plan, data_dir=tmp_path)

    # Turn was re-tagged; the surviving turn is unchanged.
    rewritten = await threads.get_turn(a_turn.turn_id)
    assert rewritten is not None
    assert rewritten.project_id == surviving.project_id
    untouched = await threads.get_turn(b_turn.turn_id)
    assert untouched is not None
    assert untouched.project_id == surviving.project_id  # was already on B

    # Memory row migrated and the lead-authored row re-anchored.
    moved = await memory.get(a_memory.memory_id)
    assert moved is not None
    assert moved.project_id == surviving.project_id
    import sqlite3

    with sqlite3.connect(memory._db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT agent_id FROM memory_entries WHERE memory_id = ?",
            (a_memory.memory_id,),
        ).fetchone()
        assert row["agent_id"] == surviving_lead.agent_id
    untouched_mem = await memory.get(b_memory.memory_id)
    assert untouched_mem is not None
    assert untouched_mem.project_id == surviving.project_id

    # Absorbed lead is archived and unlinked.
    absorbed_after = await agents.get(absorbed_lead.agent_id)
    assert absorbed_after is not None
    assert absorbed_after.status == "archived"
    assert absorbed_after.project_id is None

    # Absorbed project is archived.
    absorbed_proj = await projects.get(absorbed.project_id)
    assert absorbed_proj is not None
    assert absorbed_proj.status == "archived"
    assert absorbed_proj.lead_agent_id is None

    # Surviving project's lead pointer is unchanged; the surviving
    # project is touched.
    surviving_after = await projects.get(surviving.project_id)
    assert surviving_after is not None
    assert surviving_after.lead_agent_id == surviving_lead.agent_id
    assert surviving_after.status == "active"
    assert surviving_after.last_active_at_ms >= plan.computed_at_ms

    # Outcome reports the counts.
    assert outcome.thread_turns_rewritten == 1
    assert outcome.memory_entries_migrated == 1
    assert outcome.absorbed_lead_archived is True
    assert outcome.sub_leads_reparented == 0


async def test_apply_migrates_routing_and_drops_conflict(tmp_path: Path) -> None:
    projects, threads, memory, agents, routing = await _make_stores(tmp_path)
    absorbed = await _seed_project(projects, name="UI", slug="ui")
    surviving = await _seed_project(projects, name="Thalyn", slug="thalyn")
    await _seed_routing(routing, project=absorbed, task_tag="coding", provider_id="ollama")
    await _seed_routing(routing, project=absorbed, task_tag="research", provider_id="anthropic")
    await _seed_routing(routing, project=surviving, task_tag="coding", provider_id="anthropic")

    plan = await compute_merge_plan(
        from_project_id=absorbed.project_id,
        into_project_id=surviving.project_id,
        projects=projects,
        threads=threads,
        memory=memory,
        agents=agents,
        routing_overrides=routing,
    )
    outcome = await apply_merge_plan(plan, data_dir=tmp_path)

    # The clean migration landed; the conflict's absorbed row is gone.
    surviving_routes = await routing.list_for_project(surviving.project_id)
    routes_by_tag = {r.task_tag: r for r in surviving_routes}
    assert routes_by_tag["coding"].provider_id == "anthropic"  # surviving won
    assert routes_by_tag["research"].provider_id == "anthropic"  # migrated
    absorbed_routes = await routing.list_for_project(absorbed.project_id)
    assert absorbed_routes == []

    assert outcome.routing_overrides_migrated == 1
    assert outcome.routing_overrides_dropped == 1


async def test_apply_unions_connector_grants(tmp_path: Path) -> None:
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
    outcome = await apply_merge_plan(plan, data_dir=tmp_path)

    refreshed = await projects.get(surviving.project_id)
    assert refreshed is not None
    assert refreshed.connector_grants == {
        "slack": "channel:thalyn",  # surviving wins
        "linear": "team:ui",  # absorbed-only key migrated
    }
    assert outcome.connector_grants_updated is True


async def test_apply_writes_audit_ndjson(tmp_path: Path) -> None:
    projects, threads, memory, agents, routing = await _make_stores(tmp_path)
    absorbed = await _seed_project(projects, name="UI", slug="ui")
    surviving = await _seed_project(projects, name="Thalyn", slug="thalyn")
    thread = await _seed_thread(threads)
    await _seed_turn(threads, thread=thread, project=absorbed, body="ui")

    plan = await compute_merge_plan(
        from_project_id=absorbed.project_id,
        into_project_id=surviving.project_id,
        projects=projects,
        threads=threads,
        memory=memory,
        agents=agents,
        routing_overrides=routing,
    )
    outcome = await apply_merge_plan(plan, data_dir=tmp_path)

    log_path = Path(outcome.log_path)
    assert log_path.exists()
    lines = log_path.read_text().strip().splitlines()
    assert len(lines) == 2
    plan_entry = json.loads(lines[0])
    outcome_entry = json.loads(lines[1])
    assert plan_entry["kind"] == "plan"
    assert plan_entry["mergeId"] == plan.merge_id
    assert plan_entry["payload"]["counts"]["threadTurns"] == 1
    assert outcome_entry["kind"] == "outcome"
    assert outcome_entry["payload"]["threadTurnsRewritten"] == 1


async def test_apply_re_parents_sub_leads_when_present(tmp_path: Path) -> None:
    projects, threads, memory, agents, routing = await _make_stores(tmp_path)
    absorbed = await _seed_project(projects, name="UI", slug="ui")
    surviving = await _seed_project(projects, name="Thalyn", slug="thalyn")
    absorbed_lead = await _seed_lead(agents, projects, absorbed)
    surviving_lead = await _seed_lead(agents, projects, surviving)
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
    outcome = await apply_merge_plan(plan, data_dir=tmp_path)

    reparented = await agents.get(sub_lead.agent_id)
    assert reparented is not None
    assert reparented.parent_agent_id == surviving_lead.agent_id
    assert reparented.project_id == surviving.project_id
    assert reparented.status == "active"
    assert outcome.sub_leads_reparented == 1


async def test_apply_sweeps_in_flight_turns_to_surviving(
    tmp_path: Path,
) -> None:
    """A turn that lands on the absorbed project between plan + apply is
    rewritten too — the F3.4 invariant is `every absorbed-tagged turn
    rewrites`, full stop.

    The plan's captured ids are informational (so the renderer can show
    the user "this is what will change"); the apply re-tags by
    ``project_id`` so concurrent activity doesn't leave half-merged
    state. The outcome count reports reality (what the apply did),
    not the plan's snapshotted expectation — that gap is documented in
    the audit entry's plan-then-outcome pair.
    """
    projects, threads, memory, agents, routing = await _make_stores(tmp_path)
    absorbed = await _seed_project(projects, name="UI", slug="ui")
    surviving = await _seed_project(projects, name="Thalyn", slug="thalyn")
    thread = await _seed_thread(threads)
    a_turn = await _seed_turn(threads, thread=thread, project=absorbed, body="early ui")

    plan = await compute_merge_plan(
        from_project_id=absorbed.project_id,
        into_project_id=surviving.project_id,
        projects=projects,
        threads=threads,
        memory=memory,
        agents=agents,
        routing_overrides=routing,
    )
    # Race: a worker writes a fresh turn on the absorbed project
    # between plan + apply. The merge sweeps it in.
    late_turn = await _seed_turn(threads, thread=thread, project=absorbed, body="late ui")
    outcome = await apply_merge_plan(plan, data_dir=tmp_path)

    a_after = await threads.get_turn(a_turn.turn_id)
    assert a_after is not None
    assert a_after.project_id == surviving.project_id
    late_after = await threads.get_turn(late_turn.turn_id)
    assert late_after is not None
    # The late turn was swept under the surviving project — anything
    # else would leave dangling rows on an archived project.
    assert late_after.project_id == surviving.project_id
    # Two turns rewritten (plan saw one, apply found two — the audit
    # entry's plan/outcome pair makes the gap legible).
    assert outcome.thread_turns_rewritten == 2
