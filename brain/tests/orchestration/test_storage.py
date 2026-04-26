"""Tests for the per-run SqliteSaver storage layer."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from thalyn_brain.orchestration import Runner
from thalyn_brain.orchestration.state import RunStatus
from thalyn_brain.orchestration.storage import (
    default_data_dir,
    open_run_checkpointer,
    run_db_path,
)
from thalyn_brain.provider import AnthropicProvider, ProviderRegistry

from tests.provider._fake_sdk import factory_for, result_message, text_message


def _registry_with(provider: AnthropicProvider) -> ProviderRegistry:
    registry = ProviderRegistry()
    registry._providers["anthropic"] = provider
    return registry


def test_run_db_path_creates_runs_dir(tmp_path: Path) -> None:
    path = run_db_path("r_abc", data_dir=tmp_path)
    assert path == tmp_path / "runs" / "r_abc.db"
    assert path.parent.is_dir()


async def test_open_run_checkpointer_yields_a_saver(tmp_path: Path) -> None:
    async with open_run_checkpointer("r_test", data_dir=tmp_path) as saver:
        assert saver is not None
        # The saver exposes the LangGraph BaseCheckpointSaver surface;
        # we don't need to call any method here — opening + closing
        # without raising is the contract.


async def test_default_data_dir_falls_back_to_xdg_when_overridden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("THALYN_DATA_DIR", str(tmp_path))
    assert default_data_dir() == tmp_path


async def test_runner_persists_state_to_per_run_db(tmp_path: Path) -> None:
    """End-to-end: run the graph with a real SqliteSaver, approve the
    plan, and verify the per-run db file exists with non-trivial
    content."""
    _fake, factory = factory_for(
        [
            text_message('{"goal": "x", "steps": []}'),
            result_message(),
            text_message("Hi."),
            result_message(),
        ]
    )
    provider = AnthropicProvider(client_factory=factory)
    registry = _registry_with(provider)
    runner = Runner(registry, data_dir=tmp_path)

    captured: list[tuple[str, Any]] = []

    async def notify(method: str, params: Any) -> None:
        captured.append((method, params))

    paused = await runner.run(
        session_id="sess",
        provider_id="anthropic",
        prompt="Hello",
        notify=notify,
        run_id="r_persist_test",
    )
    assert paused.status == RunStatus.AWAITING_APPROVAL.value

    result = await runner.approve_plan(
        run_id="r_persist_test",
        provider_id="anthropic",
        decision="approve",
        notify=notify,
    )
    assert result is not None
    assert result.status == RunStatus.COMPLETED.value
    db_path = tmp_path / "runs" / "r_persist_test.db"
    assert db_path.exists(), "the per-run db file must be created"
    # The file should be non-empty — LangGraph's setup creates schema +
    # at least one snapshot.
    assert db_path.stat().st_size > 0
