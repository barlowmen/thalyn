"""End-to-end project merge with sub-leads in tow.

Verifies the F3.4 + F2.3 interaction: when a project is merged into
another and the absorbed lead had sub-leads, the sub-leads re-attach
under the surviving lead. This is the v0.36 recipe step 4 ("project
merge with sub-leads: re-attachment works"), exercised through the
real lifecycle + memory + merge stack rather than hand-crafted rows.

The acceptance is two-fold: the agent record's ``parent_agent_id``
flips, and the memory-visibility helper reflects the new parentage —
the surviving lead now reads the sub-lead's memory, the absorbed
lead's pointer is gone.
"""

from __future__ import annotations

import time
from pathlib import Path

from thalyn_brain.agents import AgentRecordsStore
from thalyn_brain.lead_lifecycle import (
    LeadLifecycle,
    SpawnRequest,
    SubLeadSpawnRequest,
)
from thalyn_brain.memory import MemoryEntry, MemoryStore, new_memory_id
from thalyn_brain.project_merge import apply_merge_plan, compute_merge_plan
from thalyn_brain.projects import Project, ProjectsStore, new_project_id
from thalyn_brain.routing import RoutingOverridesStore
from thalyn_brain.threads import (
    Thread,
    ThreadsStore,
    ThreadTurn,
    new_thread_id,
    new_turn_id,
)


def _now() -> int:
    return int(time.time() * 1000)


async def _seed_project(
    projects: ProjectsStore,
    *,
    name: str,
    slug: str,
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
        connector_grants=None,
        local_only=False,
        status="active",
        created_at_ms=_now(),
        last_active_at_ms=_now(),
    )
    await projects.insert(project)
    return project


async def _seed_thread_turn(
    threads: ThreadsStore,
    *,
    project_id: str,
    body: str,
) -> tuple[Thread, ThreadTurn]:
    thread = Thread(
        thread_id=new_thread_id(),
        user_scope="self",
        created_at_ms=_now(),
        last_active_at_ms=_now(),
    )
    await threads.insert_thread(thread)
    now = _now()
    turn = ThreadTurn(
        turn_id=new_turn_id(),
        thread_id=thread.thread_id,
        project_id=project_id,
        agent_id=None,
        role="user",
        body=body,
        provenance=None,
        confidence=None,
        episodic_index_ptr=None,
        at_ms=now,
        status="completed",
    )
    await threads.insert_turn(turn)
    return thread, turn


async def test_merge_reattaches_sub_lead_and_flips_memory_visibility(
    tmp_path: Path,
) -> None:
    """The exit-criteria recipe — full stack from spawn through merge.

    Two projects, each with a top-level lead spawned through the
    lifecycle. The absorbed project's lead also has a real sub-lead
    spawned via ``spawn_sub_lead``. Both the absorbed lead and the
    sub-lead write agent-scoped memory. After the merge, the sub-lead
    reports to the surviving lead, and the surviving lead now reads
    the sub-lead's memory while the absorbed lead does not.
    """
    agents = AgentRecordsStore(data_dir=tmp_path)
    projects = ProjectsStore(data_dir=tmp_path)
    threads = ThreadsStore(data_dir=tmp_path)
    memory = MemoryStore(data_dir=tmp_path)
    routing = RoutingOverridesStore(data_dir=tmp_path)
    lifecycle = LeadLifecycle(agents=agents, projects=projects)

    absorbed = await _seed_project(projects, name="UI", slug="ui")
    surviving = await _seed_project(projects, name="Thalyn", slug="thalyn")
    absorbed_lead = await lifecycle.spawn(
        SpawnRequest(project_id=absorbed.project_id, display_name="Lead-UI"),
    )
    surviving_lead = await lifecycle.spawn(
        SpawnRequest(project_id=surviving.project_id, display_name="Lead-Thalyn"),
    )
    sub = await lifecycle.spawn_sub_lead(
        SubLeadSpawnRequest(
            parent_agent_id=absorbed_lead.agent_id,
            scope_facet="harness",
        ),
    )

    # The absorbed project has a turn so the merge has something to
    # rewrite (the sub-lead path doesn't strictly need it, but real
    # merges always carry turns and we want a complete recipe).
    await _seed_thread_turn(
        threads,
        project_id=absorbed.project_id,
        body="UI: harness flake repro from this morning",
    )

    # Sub-lead writes its own agent memory (the kind of note a sub-lead
    # would log mid-task).
    sub_note = MemoryEntry(
        memory_id=new_memory_id(),
        project_id=absorbed.project_id,
        agent_id=sub.agent_id,
        scope="agent",
        kind="fact",
        body="harness fixture v3 lands the flake repro",
        author=sub.agent_id,
        created_at_ms=_now(),
        updated_at_ms=_now(),
    )
    await memory.insert(sub_note)

    # Pre-merge sanity: surviving lead can't see the sub-lead's memory.
    pre_visible = await memory.list_visible_for_agent(
        surviving_lead.agent_id,
        agents=agents,
    )
    assert sub_note.body not in {e.body for e in pre_visible}

    plan = await compute_merge_plan(
        from_project_id=absorbed.project_id,
        into_project_id=surviving.project_id,
        projects=projects,
        threads=threads,
        memory=memory,
        agents=agents,
        routing_overrides=routing,
    )
    assert sub.agent_id in plan.re_parent_sub_lead_ids
    outcome = await apply_merge_plan(plan, data_dir=tmp_path)
    assert outcome.sub_leads_reparented == 1
    assert outcome.absorbed_lead_archived is True

    # Sub-lead now belongs to the surviving project + lead.
    reparented = await agents.get(sub.agent_id)
    assert reparented is not None
    assert reparented.parent_agent_id == surviving_lead.agent_id
    assert reparented.project_id == surviving.project_id
    assert reparented.status == "active"

    # Memory visibility flipped: surviving lead reads the sub-lead's
    # memory, absorbed lead can no longer (it's archived anyway, but
    # the API call still works against the archived id and must return
    # nothing for the sub-lead's row since the parent edge is gone).
    post_visible_surviving = await memory.list_visible_for_agent(
        surviving_lead.agent_id,
        agents=agents,
    )
    post_visible_absorbed = await memory.list_visible_for_agent(
        absorbed_lead.agent_id,
        agents=agents,
    )
    assert sub_note.body in {e.body for e in post_visible_surviving}
    assert sub_note.body not in {e.body for e in post_visible_absorbed}


async def test_merge_with_no_sub_leads_leaves_count_zero(
    tmp_path: Path,
) -> None:
    """Sanity check: no sub-leads → ``subLeadsReparented == 0``.

    The plan's empty re-parent list shouldn't fire the apply path's
    re-parent guard. This locks in the v0.35 behaviour the v0.36 work
    extends.
    """
    agents = AgentRecordsStore(data_dir=tmp_path)
    projects = ProjectsStore(data_dir=tmp_path)
    threads = ThreadsStore(data_dir=tmp_path)
    memory = MemoryStore(data_dir=tmp_path)
    routing = RoutingOverridesStore(data_dir=tmp_path)
    lifecycle = LeadLifecycle(agents=agents, projects=projects)

    absorbed = await _seed_project(projects, name="UI", slug="ui-bare")
    surviving = await _seed_project(projects, name="Thalyn", slug="thalyn-bare")
    await lifecycle.spawn(SpawnRequest(project_id=absorbed.project_id))
    await lifecycle.spawn(SpawnRequest(project_id=surviving.project_id))

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

    assert outcome.sub_leads_reparented == 0
    assert plan.re_parent_sub_lead_ids == ()
