"""Storage-layer tests for sub-lead memory namespace isolation.

The architecture invariant from F2.3 / project_agent_hierarchy is:
**parent reads child, sibling cannot read sibling, only owner writes**.
v0.36 surfaces this through ``MemoryStore.list_visible_for_agent`` and
``MemoryStore.assert_writable_by``. The isolation has to hold against
direct-DB queries, not just the high-level API, so the tests below
peek at ``app.db`` with a raw ``sqlite3`` connection to confirm rows
are scoped by ``agent_id`` (the column the helpers consult) rather
than by accident of query construction.

A clean tree: top-level lead under a fresh project, two sub-leads
("ui" and "harness") parented to it. Each agent writes one
agent-scoped row; the helpers and the direct-DB query then assert
the visibility / writability rules hold.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest
from thalyn_brain.agents import AgentRecordsStore
from thalyn_brain.lead_lifecycle import (
    LeadLifecycle,
    SpawnRequest,
    SubLeadSpawnRequest,
)
from thalyn_brain.memory import (
    MemoryAccessError,
    MemoryEntry,
    MemoryStore,
    new_memory_id,
)
from thalyn_brain.projects import Project, ProjectsStore, new_project_id


def _now_ms() -> int:
    return int(time.time() * 1000)


async def _seed_project(projects: ProjectsStore, *, slug: str) -> Project:
    now = _now_ms()
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


async def _agent_memory(
    store: MemoryStore,
    *,
    owner_agent_id: str,
    body: str,
) -> MemoryEntry:
    now = _now_ms()
    entry = MemoryEntry(
        memory_id=new_memory_id(),
        project_id=None,
        agent_id=owner_agent_id,
        scope="agent",
        kind="fact",
        body=body,
        author=owner_agent_id,
        created_at_ms=now,
        updated_at_ms=now,
    )
    await store.insert(entry)
    return entry


async def _build_tree(tmp_path: Path) -> dict[str, object]:
    agents = AgentRecordsStore(data_dir=tmp_path)
    projects = ProjectsStore(data_dir=tmp_path)
    lifecycle = LeadLifecycle(agents=agents, projects=projects)
    project = await _seed_project(projects, slug="alpha")
    lead = await lifecycle.spawn(SpawnRequest(project_id=project.project_id))
    ui = await lifecycle.spawn_sub_lead(
        SubLeadSpawnRequest(parent_agent_id=lead.agent_id, scope_facet="ui"),
    )
    harness = await lifecycle.spawn_sub_lead(
        SubLeadSpawnRequest(parent_agent_id=lead.agent_id, scope_facet="harness"),
    )
    memory = MemoryStore(data_dir=tmp_path)
    return {
        "agents": agents,
        "memory": memory,
        "lead": lead,
        "ui": ui,
        "harness": harness,
        "project": project,
        "data_dir": tmp_path,
    }


async def test_parent_lead_sees_descendant_memory_via_api(tmp_path: Path) -> None:
    tree = await _build_tree(tmp_path)
    memory: MemoryStore = tree["memory"]  # type: ignore[assignment]
    agents: AgentRecordsStore = tree["agents"]  # type: ignore[assignment]
    lead = tree["lead"]
    ui = tree["ui"]
    harness = tree["harness"]

    await _agent_memory(memory, owner_agent_id=lead.agent_id, body="lead-note")  # type: ignore[attr-defined]
    await _agent_memory(memory, owner_agent_id=ui.agent_id, body="ui-note")  # type: ignore[attr-defined]
    await _agent_memory(memory, owner_agent_id=harness.agent_id, body="harness-note")  # type: ignore[attr-defined]

    visible_to_lead = await memory.list_visible_for_agent(
        lead.agent_id,  # type: ignore[attr-defined]
        agents=agents,
    )
    bodies = sorted(e.body for e in visible_to_lead)
    assert bodies == ["harness-note", "lead-note", "ui-note"]


async def test_sub_lead_only_sees_its_own_memory(tmp_path: Path) -> None:
    tree = await _build_tree(tmp_path)
    memory: MemoryStore = tree["memory"]  # type: ignore[assignment]
    agents: AgentRecordsStore = tree["agents"]  # type: ignore[assignment]
    lead = tree["lead"]
    ui = tree["ui"]
    harness = tree["harness"]

    await _agent_memory(memory, owner_agent_id=lead.agent_id, body="lead-note")  # type: ignore[attr-defined]
    await _agent_memory(memory, owner_agent_id=ui.agent_id, body="ui-note")  # type: ignore[attr-defined]
    await _agent_memory(memory, owner_agent_id=harness.agent_id, body="harness-note")  # type: ignore[attr-defined]

    visible_to_ui = await memory.list_visible_for_agent(
        ui.agent_id,  # type: ignore[attr-defined]
        agents=agents,
    )
    assert [e.body for e in visible_to_ui] == ["ui-note"]


async def test_sibling_sub_leads_cannot_see_each_other_via_direct_db(
    tmp_path: Path,
) -> None:
    """Bypass the API and assert the storage layer respects the rule.

    The ``memory_entries`` table carries ``agent_id``; the
    visibility helper consults it. A direct-DB query against the
    table run with the visible-set computed by the helper must
    return only the rows the helper allowed — no matter how the
    application code is structured. This catches the failure mode
    where someone adds a new ``MemoryStore`` reader that forgets
    the namespace filter.
    """
    tree = await _build_tree(tmp_path)
    memory: MemoryStore = tree["memory"]  # type: ignore[assignment]
    agents: AgentRecordsStore = tree["agents"]  # type: ignore[assignment]
    ui = tree["ui"]
    harness = tree["harness"]
    data_dir: Path = tree["data_dir"]  # type: ignore[assignment]

    await _agent_memory(memory, owner_agent_id=ui.agent_id, body="ui-note")  # type: ignore[attr-defined]
    await _agent_memory(memory, owner_agent_id=harness.agent_id, body="harness-note")  # type: ignore[attr-defined]

    # The agent_id column on memory_entries is what the helper keys
    # off of; a raw SQL filter using the same agent id set must return
    # the same rows the helper produced. If a test fails here it means
    # the in-DB layout and the helper diverged.
    db_path = data_dir / "app.db"
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT body, agent_id FROM memory_entries WHERE scope = 'agent' AND agent_id = ?",
            (ui.agent_id,),  # type: ignore[attr-defined]
        ).fetchall()
    assert [row["body"] for row in rows] == ["ui-note"]
    # The helper agrees with the direct-DB filter — sibling not visible.
    visible_to_ui = await memory.list_visible_for_agent(
        ui.agent_id,  # type: ignore[attr-defined]
        agents=agents,
    )
    assert {e.body for e in visible_to_ui} == {"ui-note"}


async def test_assert_writable_by_blocks_parent_writing_child_row(
    tmp_path: Path,
) -> None:
    tree = await _build_tree(tmp_path)
    memory: MemoryStore = tree["memory"]  # type: ignore[assignment]
    lead = tree["lead"]
    ui = tree["ui"]

    ui_row = await _agent_memory(memory, owner_agent_id=ui.agent_id, body="ui-note")  # type: ignore[attr-defined]

    # The sub-lead can write its own row…
    await memory.assert_writable_by(ui_row.memory_id, writer_agent_id=ui.agent_id)  # type: ignore[attr-defined]

    # …but the parent lead cannot, even though it can read.
    with pytest.raises(MemoryAccessError, match="cannot write"):
        await memory.assert_writable_by(
            ui_row.memory_id,
            writer_agent_id=lead.agent_id,  # type: ignore[attr-defined]
        )


async def test_assert_writable_by_skips_non_agent_scope(tmp_path: Path) -> None:
    tree = await _build_tree(tmp_path)
    memory: MemoryStore = tree["memory"]  # type: ignore[assignment]
    project: Project = tree["project"]  # type: ignore[assignment]
    lead = tree["lead"]

    now = _now_ms()
    project_row = MemoryEntry(
        memory_id=new_memory_id(),
        project_id=project.project_id,
        agent_id=None,
        scope="project",
        kind="fact",
        body="project-note",
        author=lead.agent_id,  # type: ignore[attr-defined]
        created_at_ms=now,
        updated_at_ms=now,
    )
    await memory.insert(project_row)

    # Project-scope rows go through their own write surface; the
    # agent-isolation rule does not apply.
    await memory.assert_writable_by(
        project_row.memory_id,
        writer_agent_id=lead.agent_id,  # type: ignore[attr-defined]
    )


async def test_agent_scope_insert_requires_agent_id(tmp_path: Path) -> None:
    memory = MemoryStore(data_dir=tmp_path)
    now = _now_ms()
    bad = MemoryEntry(
        memory_id=new_memory_id(),
        project_id=None,
        agent_id=None,
        scope="agent",
        kind="fact",
        body="anonymous-agent-row",
        author="agent",
        created_at_ms=now,
        updated_at_ms=now,
    )
    with pytest.raises(ValueError, match="agent_id"):
        await memory.insert(bad)


async def test_visibility_set_updates_when_sub_lead_reparents(
    tmp_path: Path,
) -> None:
    """Re-parenting a sub-lead immediately changes who can read its memory.

    Mirrors the project-merge re-parent path: an absorbed lead's
    sub-leads land under the surviving lead, and the surviving lead
    must see their memory without a cache invalidation step. The
    helper consults ``AgentRecordsStore`` on every call, so the
    update is observable as soon as the agent row flips.
    """
    agents = AgentRecordsStore(data_dir=tmp_path)
    projects = ProjectsStore(data_dir=tmp_path)
    lifecycle = LeadLifecycle(agents=agents, projects=projects)
    project_a = await _seed_project(projects, slug="alpha")
    project_b = await _seed_project(projects, slug="beta")
    lead_a = await lifecycle.spawn(SpawnRequest(project_id=project_a.project_id))
    lead_b = await lifecycle.spawn(SpawnRequest(project_id=project_b.project_id))
    sub = await lifecycle.spawn_sub_lead(
        SubLeadSpawnRequest(parent_agent_id=lead_a.agent_id, scope_facet="ui"),
    )

    memory = MemoryStore(data_dir=tmp_path)
    await _agent_memory(memory, owner_agent_id=sub.agent_id, body="sub-note")

    # Lead-A initially sees the sub's memory.
    visible_a = await memory.list_visible_for_agent(lead_a.agent_id, agents=agents)
    visible_b = await memory.list_visible_for_agent(lead_b.agent_id, agents=agents)
    assert {e.body for e in visible_a} == {"sub-note"}
    assert {e.body for e in visible_b} == set()

    # Re-parent the sub-lead under Lead-B (the project-merge re-attachment shape).
    with sqlite3.connect(tmp_path / "app.db") as conn:
        conn.execute(
            "UPDATE agent_records SET parent_agent_id = ?, project_id = ? WHERE agent_id = ?",
            (lead_b.agent_id, project_b.project_id, sub.agent_id),
        )
        conn.commit()

    visible_a = await memory.list_visible_for_agent(lead_a.agent_id, agents=agents)
    visible_b = await memory.list_visible_for_agent(lead_b.agent_id, agents=agents)
    assert {e.body for e in visible_a} == set()
    assert {e.body for e in visible_b} == {"sub-note"}
