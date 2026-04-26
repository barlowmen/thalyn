"""Tests for the plan-approval interrupt + resume contract."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from thalyn_brain.orchestration import Runner
from thalyn_brain.orchestration.state import RunStatus
from thalyn_brain.provider import AnthropicProvider, ProviderRegistry
from thalyn_brain.runs import RunsStore

from tests.provider._fake_sdk import factory_for, result_message, text_message


def _registry_with(provider: AnthropicProvider) -> ProviderRegistry:
    registry = ProviderRegistry()
    registry._providers["anthropic"] = provider
    return registry


def _captured() -> tuple[list[tuple[str, Any]], Any]:
    captured: list[tuple[str, Any]] = []

    async def notify(method: str, params: Any) -> None:
        captured.append((method, params))

    return captured, notify


async def test_run_pauses_at_interrupt_and_emits_approval_required(
    tmp_path: Path,
) -> None:
    _fake, factory = factory_for([text_message('{"goal": "x", "steps": []}'), result_message()])
    provider = AnthropicProvider(client_factory=factory)
    registry = _registry_with(provider)
    store = RunsStore(data_dir=tmp_path)
    runner = Runner(registry, runs_store=store, data_dir=tmp_path)

    captured, notify = _captured()
    result = await runner.run(
        session_id="s",
        provider_id="anthropic",
        prompt="Hello",
        notify=notify,
    )

    assert result.status == RunStatus.AWAITING_APPROVAL.value
    assert result.final_response == ""
    assert result.plan is not None

    statuses = [params["status"] for method, params in captured if method == "run.status"]
    assert statuses[-1] == RunStatus.AWAITING_APPROVAL.value

    approval_required = [params for method, params in captured if method == "run.approval_required"]
    assert len(approval_required) == 1
    assert approval_required[0]["runId"] == result.run_id
    assert approval_required[0]["gateKind"] == "plan"

    header = await store.get(result.run_id)
    assert header is not None
    assert header.status == RunStatus.AWAITING_APPROVAL.value


async def test_approve_resumes_run_to_completion(tmp_path: Path) -> None:
    _fake, factory = factory_for(
        [
            text_message('{"goal": "x", "steps": []}'),
            result_message(),
            text_message("All done."),
            result_message(),
        ]
    )
    provider = AnthropicProvider(client_factory=factory)
    registry = _registry_with(provider)
    store = RunsStore(data_dir=tmp_path)
    runner = Runner(registry, runs_store=store, data_dir=tmp_path)

    _, notify = _captured()
    paused = await runner.run(
        session_id="s",
        provider_id="anthropic",
        prompt="Hello",
        notify=notify,
    )
    assert paused.status == RunStatus.AWAITING_APPROVAL.value

    finished = await runner.approve_plan(
        run_id=paused.run_id,
        provider_id="anthropic",
        decision="approve",
        notify=notify,
    )
    assert finished is not None
    assert finished.status == RunStatus.COMPLETED.value
    assert "All done." in finished.final_response

    header = await store.get(paused.run_id)
    assert header is not None
    assert header.status == RunStatus.COMPLETED.value
    assert header.completed_at_ms is not None


async def test_edit_overwrites_plan_before_resuming(tmp_path: Path) -> None:
    _fake, factory = factory_for(
        [
            text_message('{"goal": "original", "steps": []}'),
            result_message(),
            text_message("response."),
            result_message(),
        ]
    )
    provider = AnthropicProvider(client_factory=factory)
    registry = _registry_with(provider)
    store = RunsStore(data_dir=tmp_path)
    runner = Runner(registry, runs_store=store, data_dir=tmp_path)

    captured, notify = _captured()
    paused = await runner.run(
        session_id="s",
        provider_id="anthropic",
        prompt="Hello",
        notify=notify,
    )
    edited_plan = {
        "goal": "edited goal",
        "nodes": [
            {
                "id": "step_edit",
                "order": 0,
                "description": "Edited step.",
                "rationale": "User-edited.",
                "estimatedCost": {},
                "status": "pending",
                "parentId": None,
            }
        ],
    }

    finished = await runner.approve_plan(
        run_id=paused.run_id,
        provider_id="anthropic",
        decision="edit",
        edited_plan=edited_plan,
        notify=notify,
    )
    assert finished is not None
    assert finished.status == RunStatus.COMPLETED.value
    assert finished.plan is not None
    assert finished.plan["goal"] == "edited goal"

    plan_updates = [params["plan"] for method, params in captured if method == "run.plan_update"]
    # First plan_update came from the planner; the second is the edit.
    assert any(p["goal"] == "edited goal" for p in plan_updates)


async def test_reject_marks_run_killed_without_resuming(tmp_path: Path) -> None:
    _fake, factory = factory_for([text_message('{"goal": "x", "steps": []}'), result_message()])
    provider = AnthropicProvider(client_factory=factory)
    registry = _registry_with(provider)
    store = RunsStore(data_dir=tmp_path)
    runner = Runner(registry, runs_store=store, data_dir=tmp_path)

    captured, notify = _captured()
    paused = await runner.run(
        session_id="s",
        provider_id="anthropic",
        prompt="Hello",
        notify=notify,
    )

    rejected = await runner.approve_plan(
        run_id=paused.run_id,
        provider_id="anthropic",
        decision="reject",
        notify=notify,
    )
    assert rejected is not None
    assert rejected.status == RunStatus.KILLED.value
    # The respond node should not have run, so no final response.
    assert rejected.final_response == ""

    statuses = [params["status"] for method, params in captured if method == "run.status"]
    assert RunStatus.KILLED.value in statuses


async def test_unknown_decision_raises(tmp_path: Path) -> None:
    registry = ProviderRegistry()
    runner = Runner(registry, data_dir=tmp_path)
    _, notify = _captured()
    with pytest.raises(ValueError):
        await runner.approve_plan(
            run_id="r",
            provider_id="anthropic",
            decision="maybe",
            notify=notify,
        )
