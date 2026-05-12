"""Tests for the conversational sub-lead spawn action.

Two surfaces: the matcher (regex-driven, parses both imperative and
reply-shape phrasings) and the executor (resolves the parent name,
runs the lifecycle, surfaces depth-cap as a user-friendly message
rather than crashing). The integration test wires the action into a
real ``ActionRegistry`` + ``LeadLifecycle`` so a future refactor that
splits the matcher / executor doesn't quietly break the conversational
contract.
"""

from __future__ import annotations

import time
from pathlib import Path

from thalyn_brain.action_registry import ActionRegistry
from thalyn_brain.agents import AgentRecordsStore
from thalyn_brain.lead_lifecycle import (
    LeadLifecycle,
    SpawnRequest,
    SubLeadSpawnRequest,
)
from thalyn_brain.projects import Project, ProjectsStore, new_project_id
from thalyn_brain.sub_lead_actions import (
    SUB_LEAD_SPAWN_ACTION,
    SubLeadSpawnMatcher,
    register_sub_lead_actions,
)


def _now() -> int:
    return int(time.time() * 1000)


async def _seed_project(projects: ProjectsStore, *, slug: str = "alpha") -> Project:
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
        created_at_ms=_now(),
        last_active_at_ms=_now(),
    )
    await projects.insert(project)
    return project


# -----------------------------------------------------------------
# Matcher
# -----------------------------------------------------------------


def test_matcher_recognises_imperative_phrasing() -> None:
    matcher = SubLeadSpawnMatcher()
    match = matcher.try_match(
        "spawn a sub-lead for ui under Lead-Alpha",
        context={},
    )
    assert match is not None
    assert match.action_name == SUB_LEAD_SPAWN_ACTION
    assert match.inputs["parent_lead"] == "Lead-Alpha"
    assert match.inputs["scope_facet"] == "ui"


def test_matcher_recognises_reply_shape_with_leading_address() -> None:
    matcher = SubLeadSpawnMatcher()
    match = matcher.try_match(
        "Lead-Alpha, spin up a sub-lead for harness",
        context={},
    )
    assert match is not None
    assert match.inputs["parent_lead"] == "Lead-Alpha"
    assert match.inputs["scope_facet"] == "harness"


def test_matcher_handles_multiword_facet_with_article() -> None:
    matcher = SubLeadSpawnMatcher()
    match = matcher.try_match(
        "spawn a sub-lead for the cost monitoring under Lead-Alpha.",
        context={},
    )
    assert match is not None
    # "the" is stripped so the facet matches the lifecycle's namespace
    # derivation rather than slugifying "the-cost-monitoring".
    assert match.inputs["scope_facet"] == "cost monitoring"


def test_matcher_returns_none_for_unrelated_prompt() -> None:
    matcher = SubLeadSpawnMatcher()
    assert matcher.try_match("how is the build going?", context={}) is None


def test_matcher_returns_none_for_partial_phrasing() -> None:
    matcher = SubLeadSpawnMatcher()
    # Missing the "for <facet>" clause.
    assert matcher.try_match("spawn a sub-lead under Lead-Alpha", context={}) is None


# -----------------------------------------------------------------
# Executor (via the registry)
# -----------------------------------------------------------------


async def _setup(
    tmp_path: Path,
) -> tuple[
    ActionRegistry,
    AgentRecordsStore,
    LeadLifecycle,
    ProjectsStore,
]:
    agents = AgentRecordsStore(data_dir=tmp_path)
    projects = ProjectsStore(data_dir=tmp_path)
    lifecycle = LeadLifecycle(agents=agents, projects=projects)
    registry = ActionRegistry()
    register_sub_lead_actions(registry, agents=agents, lifecycle=lifecycle)
    return registry, agents, lifecycle, projects


async def test_executor_spawns_sub_lead_under_named_parent(tmp_path: Path) -> None:
    registry, agents, lifecycle, projects = await _setup(tmp_path)
    project = await _seed_project(projects, slug="alpha")
    parent = await lifecycle.spawn(
        SpawnRequest(project_id=project.project_id, display_name="Lead-Alpha"),
    )

    result = await registry.execute(
        SUB_LEAD_SPAWN_ACTION,
        {"parent_lead": "Lead-Alpha", "scope_facet": "ui"},
    )

    assert "Spawned SubLead-Ui under Lead-Alpha" in result.confirmation
    sub_leads = await agents.list_all(kind="sub_lead", project_id=project.project_id)
    assert len(sub_leads) == 1
    assert sub_leads[0].parent_agent_id == parent.agent_id
    assert sub_leads[0].scope_facet == "ui"


async def test_executor_resolves_parent_by_unique_prefix(tmp_path: Path) -> None:
    registry, _agents, lifecycle, projects = await _setup(tmp_path)
    project = await _seed_project(projects, slug="alpha")
    await lifecycle.spawn(
        SpawnRequest(project_id=project.project_id, display_name="Lead-Alpha"),
    )

    # ``Lead-Al`` uniquely picks Lead-Alpha (the seeded ``Lead-Default``
    # has a different prefix), so the prefix lookup resolves cleanly.
    result = await registry.execute(
        SUB_LEAD_SPAWN_ACTION,
        {"parent_lead": "Lead-Al", "scope_facet": "ui"},
    )

    assert "Spawned SubLead-Ui under Lead-Alpha" in result.confirmation


async def test_executor_reports_unknown_parent(tmp_path: Path) -> None:
    registry, _agents, _lifecycle, _projects = await _setup(tmp_path)
    result = await registry.execute(
        SUB_LEAD_SPAWN_ACTION,
        {"parent_lead": "Nope", "scope_facet": "ui"},
    )
    assert "I don't know a lead" in result.confirmation


async def test_executor_reports_paused_parent(tmp_path: Path) -> None:
    registry, _agents, lifecycle, projects = await _setup(tmp_path)
    project = await _seed_project(projects, slug="alpha")
    parent = await lifecycle.spawn(
        SpawnRequest(project_id=project.project_id, display_name="Lead-Alpha"),
    )
    await lifecycle.pause(parent.agent_id)

    result = await registry.execute(
        SUB_LEAD_SPAWN_ACTION,
        {"parent_lead": "Lead-Alpha", "scope_facet": "ui"},
    )

    assert "is paused" in result.confirmation
    assert "resume" in result.confirmation


async def test_executor_surfaces_depth_cap_message(tmp_path: Path) -> None:
    registry, _agents, lifecycle, projects = await _setup(tmp_path)
    project = await _seed_project(projects, slug="alpha")
    parent = await lifecycle.spawn(
        SpawnRequest(project_id=project.project_id, display_name="Lead-Alpha"),
    )
    sub = await lifecycle.spawn_sub_lead(
        SubLeadSpawnRequest(parent_agent_id=parent.agent_id, scope_facet="ui"),
    )

    result = await registry.execute(
        SUB_LEAD_SPAWN_ACTION,
        {"parent_lead": sub.display_name, "scope_facet": "deeper"},
    )

    assert "depth" in result.confirmation
    assert "v1 caps depth at 2" in result.confirmation


async def test_executor_followup_carries_subtree_metadata(tmp_path: Path) -> None:
    registry, _agents, lifecycle, projects = await _setup(tmp_path)
    project = await _seed_project(projects, slug="alpha")
    parent = await lifecycle.spawn(
        SpawnRequest(project_id=project.project_id, display_name="Lead-Alpha"),
    )

    result = await registry.execute(
        SUB_LEAD_SPAWN_ACTION,
        {"parent_lead": "Lead-Alpha", "scope_facet": "harness"},
    )

    assert result.followup is not None
    assert result.followup["parentAgentId"] == parent.agent_id
    assert result.followup["scopeFacet"] == "harness"
    agent = result.followup["agent"]
    assert agent["kind"] == "sub_lead"
    assert agent["scopeFacet"] == "harness"
