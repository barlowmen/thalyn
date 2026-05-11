"""Tests for the conversational routing-edit path.

Now hosted in the action registry (per F9.4 / F9.5). Coverage:

- The recognised phrasings ("route X to Y", "use Y for X",
  "stop routing X", "make this project local-only", "stop being
  local-only") parse into the right ``ActionMatch``.
- Provider aliases collapse model-flavoured language ("sonnet 4.6"
  → "anthropic") to a registry key.
- Executing the matched action writes through to
  ``RoutingOverridesStore`` / ``ProjectsStore`` and returns a
  confirmation reply.
- Misses return ``None`` so the caller's reply flow runs unchanged.
- Unknown providers and missing project focus surface helpful
  refusals rather than landing dangling rows.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from thalyn_brain.action_registry import ActionRegistry
from thalyn_brain.projects import Project, ProjectsStore, new_project_id
from thalyn_brain.routing import RoutingOverridesStore
from thalyn_brain.routing_intents import (
    PROJECT_LOCAL_ONLY_ACTION,
    ROUTING_CLEAR_ACTION,
    ROUTING_SET_ACTION,
    RoutingMatcher,
    find_routing_intent,
    parse_provider_alias,
    register_routing_actions,
)


def test_parse_alias_resolves_model_names_to_provider_id() -> None:
    assert parse_provider_alias("sonnet 4.6") == "anthropic"
    assert parse_provider_alias("Opus") == "anthropic"
    assert parse_provider_alias("qwen3-coder-next") == "ollama"
    assert parse_provider_alias("MLX") == "mlx"
    assert parse_provider_alias("gpt-4o") == "openai_compat"
    assert parse_provider_alias("nope") is None


def test_intent_parser_handles_route_to_phrasing() -> None:
    intent = find_routing_intent("route coding tasks to ollama in this project")
    assert intent == ("set", "coding", "ollama")


def test_intent_parser_handles_use_for_phrasing() -> None:
    intent = find_routing_intent("use sonnet 4.6 for coding")
    assert intent == ("set", "coding", "anthropic")


def test_intent_parser_recognizes_clear_phrasings() -> None:
    assert find_routing_intent("stop routing coding") == ("clear", "coding", "")
    assert find_routing_intent("clear research") == ("clear", "research", "")


def test_intent_parser_recognizes_local_only_phrasings() -> None:
    assert find_routing_intent("make this project local-only") == ("local_only_on", "", "")
    assert find_routing_intent("Stop being local only") == ("local_only_off", "", "")


def test_intent_parser_returns_none_for_unmatched_input() -> None:
    assert find_routing_intent("hello, how's the build going?") is None


def test_matcher_returns_set_action_with_inputs() -> None:
    matcher = RoutingMatcher()
    match = matcher.try_match(
        "route coding to ollama in this project",
        context={"project_id": "proj_x"},
    )
    assert match is not None
    assert match.action_name == ROUTING_SET_ACTION
    assert match.inputs == {
        "task_tag": "coding",
        "provider_id": "ollama",
        "project_id": "proj_x",
    }


def test_matcher_returns_clear_action() -> None:
    matcher = RoutingMatcher()
    match = matcher.try_match(
        "stop routing coding",
        context={"project_id": "proj_x"},
    )
    assert match is not None
    assert match.action_name == ROUTING_CLEAR_ACTION
    assert match.inputs == {"task_tag": "coding", "project_id": "proj_x"}


def test_matcher_returns_local_only_action() -> None:
    matcher = RoutingMatcher()
    on = matcher.try_match(
        "make this project local-only",
        context={"project_id": "proj_x"},
    )
    off = matcher.try_match(
        "turn off local-only",
        context={"project_id": "proj_x"},
    )
    assert on is not None and on.action_name == PROJECT_LOCAL_ONLY_ACTION
    assert on.inputs == {"value": True, "project_id": "proj_x"}
    assert off is not None and off.action_name == PROJECT_LOCAL_ONLY_ACTION
    assert off.inputs == {"value": False, "project_id": "proj_x"}


def test_matcher_returns_none_for_non_routing_prompts() -> None:
    matcher = RoutingMatcher()
    assert (
        matcher.try_match(
            "what's the status on the auth refactor?",
            context={"project_id": "proj_x"},
        )
        is None
    )


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


def _build_registry(
    tmp_path: Path,
    *,
    valid_provider_ids: set[str] | None = None,
) -> tuple[ActionRegistry, ProjectsStore, RoutingOverridesStore]:
    projects = ProjectsStore(data_dir=tmp_path)
    overrides = RoutingOverridesStore(data_dir=tmp_path)
    registry = ActionRegistry()
    register_routing_actions(
        registry,
        overrides_store=overrides,
        projects_store=projects,
        valid_provider_ids=(
            valid_provider_ids or {"anthropic", "ollama", "mlx", "openai_compat", "llama_cpp"}
        ),
    )
    return registry, projects, overrides


@pytest.mark.asyncio
async def test_registry_writes_override_for_matching_intent(tmp_path: Path) -> None:
    registry, projects, overrides = _build_registry(tmp_path)
    project = await _seed_project(projects)

    match = registry.try_match(
        "Thalyn, route coding to ollama in this project.",
        context={"project_id": project.project_id},
    )
    assert match is not None
    assert match.action_name == ROUTING_SET_ACTION
    result = await registry.execute(match.action_name, match.inputs)
    assert "coding" in result.confirmation
    assert "ollama" in result.confirmation

    stored = await overrides.list_for_project(project.project_id)
    assert len(stored) == 1
    assert stored[0].task_tag == "coding"
    assert stored[0].provider_id == "ollama"


@pytest.mark.asyncio
async def test_registry_clears_override(tmp_path: Path) -> None:
    registry, projects, overrides = _build_registry(tmp_path)
    project = await _seed_project(projects)

    set_match = registry.try_match(
        "route coding to ollama",
        context={"project_id": project.project_id},
    )
    assert set_match is not None
    await registry.execute(set_match.action_name, set_match.inputs)

    clear_match = registry.try_match(
        "stop routing coding",
        context={"project_id": project.project_id},
    )
    assert clear_match is not None
    assert clear_match.action_name == ROUTING_CLEAR_ACTION
    await registry.execute(clear_match.action_name, clear_match.inputs)

    stored = await overrides.list_for_project(project.project_id)
    assert stored == []


@pytest.mark.asyncio
async def test_registry_flips_local_only(tmp_path: Path) -> None:
    registry, projects, _overrides = _build_registry(tmp_path)
    project = await _seed_project(projects)

    on_match = registry.try_match(
        "make this project local-only",
        context={"project_id": project.project_id},
    )
    assert on_match is not None
    await registry.execute(on_match.action_name, on_match.inputs)

    fetched = await projects.get(project.project_id)
    assert fetched is not None
    assert fetched.local_only is True

    off_match = registry.try_match(
        "turn off local-only",
        context={"project_id": project.project_id},
    )
    assert off_match is not None
    await registry.execute(off_match.action_name, off_match.inputs)
    fetched = await projects.get(project.project_id)
    assert fetched is not None
    assert fetched.local_only is False


@pytest.mark.asyncio
async def test_registry_refuses_unknown_provider(tmp_path: Path) -> None:
    registry, projects, overrides = _build_registry(
        tmp_path,
        valid_provider_ids={"anthropic"},
    )
    project = await _seed_project(projects)

    match = registry.try_match(
        "route coding to ollama",
        context={"project_id": project.project_id},
    )
    assert match is not None
    result = await registry.execute(match.action_name, match.inputs)
    # No override was written; the confirmation explains the refusal.
    assert "anthropic" in result.confirmation
    assert await overrides.list_for_project(project.project_id) == []


@pytest.mark.asyncio
async def test_registry_replies_helpfully_when_no_project_in_focus(tmp_path: Path) -> None:
    registry, _projects, _overrides = _build_registry(tmp_path)
    match = registry.try_match("route coding to ollama", context={})
    assert match is not None
    result = await registry.execute(match.action_name, match.inputs)
    assert "project" in result.confirmation.lower()
