"""Tests for the conversational connector-setup action.

Coverage mirrors F9.5's narrative path:

- The matcher recognises "set up Slack" / "install Google Calendar"
  / "connect office" and resolves the target against the catalog.
- The executor lands an install row through ``McpManager.install``
  and emits an ``ActionResult.followup`` payload carrying the
  homepage URL + required-secret schema so the in-app browser
  drawer can open at the right place.
- Already-installed connectors surface a "refresh credentials" hint
  rather than failing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from thalyn_brain.action_registry import ActionRegistry
from thalyn_brain.connector_actions import (
    CONNECTOR_SETUP_ACTION,
    ConnectorSetupMatcher,
    register_connector_actions,
)
from thalyn_brain.mcp import ConnectorRegistry, McpManager, builtin_catalog


async def _opener(
    _descriptor: dict[str, Any], _secrets: dict[str, str]
) -> Any:  # pragma: no cover - not exercised in these tests
    raise AssertionError("connector setup actions don't start a live session")


def _build_manager(tmp_path: Path) -> McpManager:
    registry = ConnectorRegistry(data_dir=tmp_path)
    return McpManager(
        registry=registry,
        catalog=builtin_catalog(),
        session_opener=_opener,
    )


def test_matcher_resolves_display_name(tmp_path: Path) -> None:
    manager = _build_manager(tmp_path)
    matcher = ConnectorSetupMatcher(manager)
    match = matcher.try_match("Thalyn, set up Slack for me.", context={})
    assert match is not None
    assert match.action_name == CONNECTOR_SETUP_ACTION
    assert match.inputs == {"connector_id": "slack"}


def test_matcher_resolves_connector_id_directly(tmp_path: Path) -> None:
    manager = _build_manager(tmp_path)
    matcher = ConnectorSetupMatcher(manager)
    match = matcher.try_match("install google_calendar", context={})
    assert match is not None
    assert match.inputs == {"connector_id": "google_calendar"}


def test_matcher_falls_back_to_prefix(tmp_path: Path) -> None:
    manager = _build_manager(tmp_path)
    matcher = ConnectorSetupMatcher(manager)
    match = matcher.try_match("connect Microsoft", context={})
    assert match is not None
    assert match.inputs == {"connector_id": "office"}


def test_matcher_returns_none_for_unknown_target(tmp_path: Path) -> None:
    manager = _build_manager(tmp_path)
    matcher = ConnectorSetupMatcher(manager)
    assert matcher.try_match("set up Atlas Tracker", context={}) is None


def test_matcher_returns_none_for_unrelated_prompts(tmp_path: Path) -> None:
    manager = _build_manager(tmp_path)
    matcher = ConnectorSetupMatcher(manager)
    assert matcher.try_match("what's on my plate today?", context={}) is None


@pytest.mark.asyncio
async def test_executor_installs_and_emits_homepage_followup(tmp_path: Path) -> None:
    manager = _build_manager(tmp_path)
    registry = ActionRegistry()
    register_connector_actions(registry, manager=manager)

    match = registry.try_match("Thalyn, set up Slack", context={})
    assert match is not None
    result = await registry.execute(match.action_name, match.inputs)
    assert "Slack" in result.confirmation
    assert "secrets" in result.confirmation.lower()

    installed = await manager.list_installed()
    assert [c.record.connector_id for c in installed] == ["slack"]

    assert result.followup is not None
    assert result.followup["connectorId"] == "slack"
    assert result.followup["alreadyInstalled"] is False
    # The homepage URL is what the in-app browser drawer opens.
    assert isinstance(result.followup["homepage"], str)
    assert result.followup["homepage"].startswith("https://")
    # Required-secret schema rides with the followup so the renderer
    # can pre-render the paste fields.
    secret_keys = {auth["key"] for auth in result.followup["requiredSecrets"]}
    assert {"bot_token", "team_id"}.issubset(secret_keys)


@pytest.mark.asyncio
async def test_executor_surfaces_already_installed_hint(tmp_path: Path) -> None:
    manager = _build_manager(tmp_path)
    await manager.install("slack")

    registry = ActionRegistry()
    register_connector_actions(registry, manager=manager)
    match = registry.try_match("set up Slack", context={})
    assert match is not None
    result = await registry.execute(match.action_name, match.inputs)
    assert "already installed" in result.confirmation.lower()
    assert result.followup is not None
    assert result.followup["alreadyInstalled"] is True
