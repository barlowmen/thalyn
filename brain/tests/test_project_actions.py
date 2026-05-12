"""Tests for the conversational ``project.merge`` action.

The matcher recognises "merge / move A into B" phrasings; the
executor resolves names against the project store and runs the full
plan + apply. Project name resolution covers exact-name / slug /
unique-prefix paths; ambiguity surfaces as a useful message rather
than a silent pick.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from thalyn_brain.action_registry import ActionRegistry
from thalyn_brain.agents import AgentRecordsStore
from thalyn_brain.lead_lifecycle import LeadLifecycle, SpawnRequest
from thalyn_brain.memory import MemoryStore
from thalyn_brain.project_actions import (
    PROJECT_MERGE_ACTION,
    PROJECT_MERGE_HARD_GATE_KIND,
    ProjectMergeMatcher,
    register_project_actions,
)
from thalyn_brain.projects import Project, ProjectsStore
from thalyn_brain.routing import RoutingOverridesStore
from thalyn_brain.threads import (
    Thread,
    ThreadsStore,
    ThreadTurn,
    new_thread_id,
    new_turn_id,
)


def _now_ms() -> int:
    return int(time.time() * 1000)


async def _setup(
    tmp_path: Path,
) -> tuple[
    ActionRegistry,
    ProjectsStore,
    ThreadsStore,
    AgentRecordsStore,
    LeadLifecycle,
]:
    projects = ProjectsStore(data_dir=tmp_path)
    agents = AgentRecordsStore(data_dir=tmp_path)
    threads = ThreadsStore(data_dir=tmp_path)
    memory = MemoryStore(data_dir=tmp_path)
    routing_overrides = RoutingOverridesStore(data_dir=tmp_path)
    lifecycle = LeadLifecycle(agents=agents, projects=projects)
    registry = ActionRegistry()
    register_project_actions(
        registry,
        projects=projects,
        threads=threads,
        memory=memory,
        agents=agents,
        routing_overrides=routing_overrides,
        data_dir=tmp_path,
    )
    return registry, projects, threads, agents, lifecycle


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
    project: Project,
    body: str,
) -> ThreadTurn:
    turn = ThreadTurn(
        turn_id=new_turn_id(),
        thread_id=thread.thread_id,
        project_id=project.project_id,
        agent_id=None,
        role="user",
        body=body,
        provenance=None,
        confidence=None,
        episodic_index_ptr=None,
        at_ms=_now_ms(),
    )
    await threads.insert_turn(turn)
    return turn


def test_matcher_captures_from_and_to_names() -> None:
    matcher = ProjectMergeMatcher()
    match = matcher.try_match("Thalyn, merge UI into Thalyn", context={})
    assert match is not None
    assert match.action_name == PROJECT_MERGE_ACTION
    assert match.inputs["from_project"] == "UI"
    assert match.inputs["into_project"] == "Thalyn"
    assert match.preview is not None
    assert "UI" in match.preview
    assert "Thalyn" in match.preview


def test_matcher_handles_move_synonym() -> None:
    matcher = ProjectMergeMatcher()
    match = matcher.try_match("move Tax Prep 2026 to Finance", context={})
    assert match is not None
    assert match.inputs["from_project"] == "Tax Prep 2026"
    assert match.inputs["into_project"] == "Finance"


def test_matcher_ignores_unrelated_prompts() -> None:
    matcher = ProjectMergeMatcher()
    assert matcher.try_match("how do I merge two PDFs?", context={}) is None
    assert matcher.try_match("hello", context={}) is None


def test_action_is_registered_as_hard_gate(tmp_path: Path) -> None:
    """Schedule.now and merge actions both want explicit approval; this
    catches a regression where hard-gating drops off the registration."""
    import asyncio

    async def runner() -> None:
        registry, *_ = await _setup(tmp_path)
        summaries = registry.list_summaries()
        merge_summary = next(s for s in summaries if s.name == PROJECT_MERGE_ACTION)
        assert merge_summary.hard_gate is True
        described = registry.describe(PROJECT_MERGE_ACTION)
        assert described["hardGate"] is True
        assert described["hardGateKind"] == PROJECT_MERGE_HARD_GATE_KIND

    asyncio.run(runner())


async def test_executor_runs_full_merge_and_archives_absorbed(tmp_path: Path) -> None:
    registry, projects, threads, _agents, lifecycle = await _setup(tmp_path)
    absorbed = await projects.create(name="UI")
    surviving = await projects.create(name="Thalyn")
    await lifecycle.spawn(SpawnRequest(project_id=absorbed.project_id))
    await lifecycle.spawn(SpawnRequest(project_id=surviving.project_id))
    thread = await _seed_thread(threads)
    await _seed_turn(threads, thread=thread, project=absorbed, body="ui work")

    result = await registry.execute(
        PROJECT_MERGE_ACTION,
        {"from_project": "UI", "into_project": "Thalyn"},
        hard_gate_resolved=True,
    )

    assert "Merged 'UI' into 'Thalyn'" in result.confirmation
    assert result.followup is not None
    assert result.followup["fromProjectId"] == absorbed.project_id
    assert result.followup["intoProjectId"] == surviving.project_id
    absorbed_after = await projects.get(absorbed.project_id)
    assert absorbed_after is not None
    assert absorbed_after.status == "archived"


async def test_executor_resolves_partial_name_when_unique(tmp_path: Path) -> None:
    registry, projects, _threads, _agents, lifecycle = await _setup(tmp_path)
    absorbed = await projects.create(name="UI Project")
    surviving = await projects.create(name="Thalyn Main")
    await lifecycle.spawn(SpawnRequest(project_id=absorbed.project_id))
    await lifecycle.spawn(SpawnRequest(project_id=surviving.project_id))

    result = await registry.execute(
        PROJECT_MERGE_ACTION,
        {"from_project": "UI", "into_project": "Thalyn"},
        hard_gate_resolved=True,
    )

    assert "Merged 'UI Project' into 'Thalyn Main'" in result.confirmation


async def test_executor_refuses_ambiguous_prefix(tmp_path: Path) -> None:
    registry, projects, _threads, _agents, lifecycle = await _setup(tmp_path)
    absorbed = await projects.create(name="UI Project")
    other = await projects.create(name="UI Components")
    surviving = await projects.create(name="Thalyn")
    await lifecycle.spawn(SpawnRequest(project_id=absorbed.project_id))
    await lifecycle.spawn(SpawnRequest(project_id=other.project_id))
    await lifecycle.spawn(SpawnRequest(project_id=surviving.project_id))

    result = await registry.execute(
        PROJECT_MERGE_ACTION,
        {"from_project": "UI", "into_project": "Thalyn"},
        hard_gate_resolved=True,
    )

    assert "could be any of" in result.confirmation
    assert "UI Components" in result.confirmation
    assert "UI Project" in result.confirmation
    # Neither absorbed nor other got archived because we refused.
    absorbed_after = await projects.get(absorbed.project_id)
    assert absorbed_after is not None
    assert absorbed_after.status == "active"


async def test_executor_refuses_self_merge(tmp_path: Path) -> None:
    registry, projects, _threads, _agents, lifecycle = await _setup(tmp_path)
    solo = await projects.create(name="Solo")
    await lifecycle.spawn(SpawnRequest(project_id=solo.project_id))

    result = await registry.execute(
        PROJECT_MERGE_ACTION,
        {"from_project": "Solo", "into_project": "Solo"},
        hard_gate_resolved=True,
    )

    assert "same project" in result.confirmation
    refreshed = await projects.get(solo.project_id)
    assert refreshed is not None
    assert refreshed.status == "active"


async def test_executor_refuses_unknown_project(tmp_path: Path) -> None:
    registry, projects, _threads, _agents, lifecycle = await _setup(tmp_path)
    real = await projects.create(name="Real")
    await lifecycle.spawn(SpawnRequest(project_id=real.project_id))

    result = await registry.execute(
        PROJECT_MERGE_ACTION,
        {"from_project": "Ghost", "into_project": "Real"},
        hard_gate_resolved=True,
    )

    assert "Ghost" in result.confirmation
    assert "don't have a project" in result.confirmation


def test_action_summary_appears_in_registry_list(tmp_path: Path) -> None:
    import asyncio

    async def runner() -> Any:
        registry, *_ = await _setup(tmp_path)
        return registry.list_summaries()

    summaries = asyncio.run(runner())
    names = {s.name for s in summaries}
    assert PROJECT_MERGE_ACTION in names
