"""Tests for restart-resume behaviour."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from thalyn_brain.orchestration import Runner
from thalyn_brain.orchestration.resume import resume_unfinished_runs
from thalyn_brain.orchestration.state import RunStatus
from thalyn_brain.provider import AnthropicProvider, ProviderRegistry
from thalyn_brain.runs import RunHeader, RunsStore

from tests.provider._fake_sdk import factory_for, result_message, text_message


def _registry_with(provider: AnthropicProvider) -> ProviderRegistry:
    registry = ProviderRegistry()
    registry._providers["anthropic"] = provider
    return registry


async def _make_runner_with_run(
    tmp_path: Path,
    *,
    finalize: bool,
) -> tuple[RunsStore, Runner, str]:
    """Drive a run to completion (finalize=True) or simulate a crash
    by inserting a header without ever invoking the runner
    (finalize=False)."""
    _fake, factory = factory_for(
        [
            text_message('{"goal": "x", "steps": []}'),
            result_message(),
            text_message("done."),
            result_message(),
        ]
    )
    provider = AnthropicProvider(client_factory=factory)
    registry = _registry_with(provider)
    store = RunsStore(data_dir=tmp_path)
    runner = Runner(registry, runs_store=store, data_dir=tmp_path)

    if finalize:

        async def notify(_method: str, _params: Any) -> None:
            return None

        paused = await runner.run(
            session_id="s",
            provider_id="anthropic",
            prompt="Hello",
            notify=notify,
        )
        result = await runner.approve_plan(
            run_id=paused.run_id,
            provider_id="anthropic",
            decision="approve",
            notify=notify,
        )
        assert result is not None
        return store, runner, result.run_id

    # Simulate a crash: insert a PLANNING header but never run.
    crashed_id = "r_crashed_123"
    await store.insert(
        RunHeader(
            run_id=crashed_id,
            project_id=None,
            parent_run_id=None,
            status=RunStatus.PLANNING.value,
            title="Hello",
            provider_id="anthropic",
            started_at_ms=int(time.time() * 1000) - 5_000,
            completed_at_ms=None,
            drift_score=0.0,
            final_response="",
        )
    )
    return store, runner, crashed_id


async def test_resume_marks_unfinished_run_errored_when_no_checkpoint(
    tmp_path: Path,
) -> None:
    store, runner, crashed_id = await _make_runner_with_run(tmp_path, finalize=False)

    touched = await resume_unfinished_runs(store, runner)
    assert crashed_id in touched

    header = await store.get(crashed_id)
    assert header is not None
    assert header.status == RunStatus.ERRORED.value
    assert header.completed_at_ms is not None
    assert "no checkpoint" in header.final_response


async def test_resume_no_op_when_runs_index_is_empty(tmp_path: Path) -> None:
    _fake, factory = factory_for([text_message('{"goal": "x", "steps": []}'), result_message()])
    provider = AnthropicProvider(client_factory=factory)
    registry = _registry_with(provider)
    store = RunsStore(data_dir=tmp_path)
    runner = Runner(registry, runs_store=store, data_dir=tmp_path)

    touched = await resume_unfinished_runs(store, runner)
    assert touched == []


async def test_already_completed_runs_are_left_alone(tmp_path: Path) -> None:
    store, runner, run_id = await _make_runner_with_run(tmp_path, finalize=True)

    header_before = await store.get(run_id)
    assert header_before is not None
    assert header_before.status == RunStatus.COMPLETED.value

    touched = await resume_unfinished_runs(store, runner)
    assert touched == []

    header_after = await store.get(run_id)
    assert header_after is not None
    assert header_after.status == RunStatus.COMPLETED.value
