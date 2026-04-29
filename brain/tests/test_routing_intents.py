"""Tests for the conversational routing-edit path.

The intent parser and the action dispatcher together are the
action-registry stub for routing edits (per ADR-0023). Coverage:

- The recognized phrasings ("route X to Y", "use Y for X",
  "stop routing X", "make this project local-only", "stop being
  local-only") parse into the right intent.
- Provider aliases collapse model-flavored language ("sonnet 4.6"
  → "anthropic") to a registry key.
- Dispatching writes through to ``RoutingOverridesStore`` /
  ``ProjectsStore`` and returns a confirmation reply.
- Misses return ``None`` so the caller's reply flow runs unchanged.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from thalyn_brain.projects import Project, ProjectsStore, new_project_id
from thalyn_brain.routing import RoutingOverridesStore
from thalyn_brain.routing_intents import (
    RoutingActionsDispatcher,
    find_routing_intent,
    parse_provider_alias,
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


def _build_dispatcher(
    tmp_path: Path,
    *,
    valid_provider_ids: set[str] | None = None,
) -> tuple[RoutingActionsDispatcher, ProjectsStore, RoutingOverridesStore]:
    projects = ProjectsStore(data_dir=tmp_path)
    overrides = RoutingOverridesStore(data_dir=tmp_path)
    dispatcher = RoutingActionsDispatcher(
        overrides_store=overrides,
        projects_store=projects,
        valid_provider_ids=valid_provider_ids
        or {"anthropic", "ollama", "mlx", "openai_compat", "llama_cpp"},
    )
    return dispatcher, projects, overrides


@pytest.mark.asyncio
async def test_dispatcher_writes_override_for_matching_intent(tmp_path: Path) -> None:
    dispatcher, projects, overrides = _build_dispatcher(tmp_path)
    project = await _seed_project(projects)

    result = await dispatcher.dispatch(
        "Thalyn, route coding to ollama in this project.",
        project_id=project.project_id,
    )
    assert result is not None
    assert result.action == "set"
    assert "coding" in result.confirmation
    assert "ollama" in result.confirmation

    stored = await overrides.list_for_project(project.project_id)
    assert len(stored) == 1
    assert stored[0].task_tag == "coding"
    assert stored[0].provider_id == "ollama"


@pytest.mark.asyncio
async def test_dispatcher_clears_override(tmp_path: Path) -> None:
    dispatcher, projects, overrides = _build_dispatcher(tmp_path)
    project = await _seed_project(projects)

    await dispatcher.dispatch(
        "route coding to ollama",
        project_id=project.project_id,
    )
    cleared = await dispatcher.dispatch(
        "stop routing coding",
        project_id=project.project_id,
    )
    assert cleared is not None
    assert cleared.action == "clear"

    stored = await overrides.list_for_project(project.project_id)
    assert stored == []


@pytest.mark.asyncio
async def test_dispatcher_flips_local_only(tmp_path: Path) -> None:
    dispatcher, projects, _overrides = _build_dispatcher(tmp_path)
    project = await _seed_project(projects)

    on = await dispatcher.dispatch(
        "make this project local-only",
        project_id=project.project_id,
    )
    assert on is not None
    assert on.action == "local_only_on"

    fetched = await projects.get(project.project_id)
    assert fetched is not None
    assert fetched.local_only is True

    off = await dispatcher.dispatch(
        "turn off local-only",
        project_id=project.project_id,
    )
    assert off is not None
    assert off.action == "local_only_off"
    fetched = await projects.get(project.project_id)
    assert fetched is not None
    assert fetched.local_only is False


@pytest.mark.asyncio
async def test_dispatcher_refuses_unknown_provider(tmp_path: Path) -> None:
    dispatcher, projects, overrides = _build_dispatcher(
        tmp_path,
        valid_provider_ids={"anthropic"},
    )
    project = await _seed_project(projects)

    result = await dispatcher.dispatch(
        "route coding to ollama",
        project_id=project.project_id,
    )
    assert result is not None
    assert result.action == "set"
    # No override was written; the confirmation explains the refusal.
    assert "anthropic" in result.confirmation
    assert await overrides.list_for_project(project.project_id) == []


@pytest.mark.asyncio
async def test_dispatcher_returns_none_for_non_routing_prompts(tmp_path: Path) -> None:
    dispatcher, projects, _overrides = _build_dispatcher(tmp_path)
    project = await _seed_project(projects)

    result = await dispatcher.dispatch(
        "what's the status on the auth refactor?",
        project_id=project.project_id,
    )
    assert result is None


@pytest.mark.asyncio
async def test_dispatcher_replies_helpfully_when_no_project_in_focus(tmp_path: Path) -> None:
    dispatcher, _projects, _overrides = _build_dispatcher(tmp_path)
    result = await dispatcher.dispatch(
        "route coding to ollama",
        project_id=None,
    )
    assert result is not None
    assert "project" in result.confirmation.lower()
