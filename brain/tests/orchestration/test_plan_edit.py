"""Tests for plan-edit propagation through the graph + runs index."""

from __future__ import annotations

from pathlib import Path
from typing import Any

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


def _multi_step_edit() -> dict[str, Any]:
    return {
        "goal": "Refactor middleware",
        "nodes": [
            {
                "id": "step_1",
                "order": 0,
                "description": "Audit existing call sites.",
                "rationale": "Need full picture before changes.",
                "estimatedCost": {"tokens": 800},
                "status": "pending",
                "parentId": None,
            },
            {
                "id": "step_2",
                "order": 1,
                "description": "Replace adapter shape.",
                "rationale": "Apply the new interface.",
                "estimatedCost": {"tokens": 1500},
                "status": "pending",
                "parentId": None,
            },
            {
                "id": "step_3",
                "order": 2,
                "description": "Verify regression suite.",
                "rationale": "Confirm parity post-rewrite.",
                "estimatedCost": {"tokens": 400},
                "status": "pending",
                "parentId": None,
            },
        ],
    }


async def test_multi_step_edit_round_trips_through_state_and_index(
    tmp_path: Path,
) -> None:
    _fake, factory = factory_for(
        [
            text_message('{"goal": "original", "steps": []}'),
            result_message(),
            text_message("acknowledged."),
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
    assert paused.status == RunStatus.AWAITING_APPROVAL.value

    edit = _multi_step_edit()
    finished = await runner.approve_plan(
        run_id=paused.run_id,
        provider_id="anthropic",
        decision="edit",
        edited_plan=edit,
        notify=notify,
    )
    assert finished is not None
    assert finished.status == RunStatus.COMPLETED.value
    assert finished.plan is not None
    assert finished.plan["goal"] == "Refactor middleware"
    assert len(finished.plan["nodes"]) == 3
    descriptions = [node["description"] for node in finished.plan["nodes"]]
    assert descriptions == [
        "Audit existing call sites.",
        "Replace adapter shape.",
        "Verify regression suite.",
    ]

    # The inspector receives the edited plan via run.plan_update.
    plan_updates = [params["plan"] for method, params in captured if method == "run.plan_update"]
    assert plan_updates[-1]["goal"] == "Refactor middleware"

    # The runs index header reflects the edited plan, not the original.
    header = await store.get(paused.run_id)
    assert header is not None
    assert header.plan is not None
    assert header.plan["goal"] == "Refactor middleware"
    assert len(header.plan["nodes"]) == 3


async def test_edit_preserves_estimated_cost_and_rationale(tmp_path: Path) -> None:
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
    runner = Runner(registry, data_dir=tmp_path)

    _, notify = _captured()
    paused = await runner.run(
        session_id="s",
        provider_id="anthropic",
        prompt="Hello",
        notify=notify,
    )

    finished = await runner.approve_plan(
        run_id=paused.run_id,
        provider_id="anthropic",
        decision="edit",
        edited_plan=_multi_step_edit(),
        notify=notify,
    )

    assert finished is not None
    assert finished.plan is not None
    nodes = finished.plan["nodes"]
    assert nodes[0]["rationale"] == "Need full picture before changes."
    assert nodes[0]["estimatedCost"] == {"tokens": 800}
    assert nodes[1]["estimatedCost"] == {"tokens": 1500}


async def test_edit_with_zero_steps_still_resumes(tmp_path: Path) -> None:
    """A user can submit an "approve as is" by editing back to a
    zero-step plan; the runner should still complete the run and
    record the (empty) plan."""
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
    runner = Runner(registry, data_dir=tmp_path)

    _, notify = _captured()
    paused = await runner.run(
        session_id="s",
        provider_id="anthropic",
        prompt="Hello",
        notify=notify,
    )

    empty_plan = {"goal": "no steps required", "nodes": []}
    finished = await runner.approve_plan(
        run_id=paused.run_id,
        provider_id="anthropic",
        decision="edit",
        edited_plan=empty_plan,
        notify=notify,
    )

    assert finished is not None
    assert finished.status == RunStatus.COMPLETED.value
    assert finished.plan is not None
    assert finished.plan["nodes"] == []
    assert finished.plan["goal"] == "no steps required"
