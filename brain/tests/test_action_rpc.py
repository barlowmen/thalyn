"""Tests for the ``action.*`` JSON-RPC surface.

Coverage:

- ``action.list`` returns the lean (name + description + hardGate)
  summary so the renderer's discovery surface stays cheap.
- ``action.describe`` returns the full input schema for one action.
- ``action.approve`` runs the executor with ``hard_gate_resolved`` so
  the hard-gate guard doesn't block the post-approval execution.
- ``action.reject`` flips the entry to ``rejected`` without running.
- Unknown / already-resolved ids surface ``INVALID_PARAMS`` errors.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest
from thalyn_brain.action_registry import (
    Action,
    ActionInput,
    ActionRegistry,
    ActionResult,
)
from thalyn_brain.action_rpc import register_action_methods
from thalyn_brain.pending_actions import PendingActionStore
from thalyn_brain.rpc import Dispatcher


def _register_test_actions(registry: ActionRegistry, recorded: list[Mapping[str, Any]]) -> None:
    async def low_risk_executor(inputs: Mapping[str, Any]) -> ActionResult:
        recorded.append(dict(inputs))
        return ActionResult(confirmation="did the thing")

    async def hard_gate_executor(inputs: Mapping[str, Any]) -> ActionResult:
        recorded.append(dict(inputs))
        return ActionResult(
            confirmation=f"sent message to {inputs['to']}",
            followup={"deliveryId": "msg_42"},
        )

    registry.register(
        Action(
            name="test.low_risk",
            description="A vanilla configurable surface.",
            inputs=(ActionInput(name="payload", description="payload"),),
            executor=low_risk_executor,
        )
    )
    registry.register(
        Action(
            name="external.send",
            description="Send a message on the user's behalf.",
            inputs=(
                ActionInput(name="to", description="recipient"),
                ActionInput(name="body", description="body"),
            ),
            executor=hard_gate_executor,
            hard_gate=True,
            hard_gate_kind="external_send",
        )
    )


@pytest.mark.asyncio
async def test_action_list_returns_summary_shape() -> None:
    registry = ActionRegistry()
    _register_test_actions(registry, recorded=[])
    pending = PendingActionStore()
    dispatcher = Dispatcher()
    register_action_methods(dispatcher, registry=registry, pending_actions=pending)

    async def _noop(method: str, params: Any) -> None:  # pragma: no cover
        return None

    response = await dispatcher.handle(
        {"jsonrpc": "2.0", "id": 1, "method": "action.list", "params": {}},
        _noop,
    )
    assert response is not None
    result = response["result"]
    by_name = {a["name"]: a for a in result["actions"]}
    assert "external.send" in by_name
    assert by_name["external.send"]["hardGate"] is True
    assert by_name["test.low_risk"]["hardGate"] is False
    # No input schema in the summary.
    assert "inputs" not in by_name["external.send"]


@pytest.mark.asyncio
async def test_action_describe_returns_full_schema() -> None:
    registry = ActionRegistry()
    _register_test_actions(registry, recorded=[])
    pending = PendingActionStore()
    dispatcher = Dispatcher()
    register_action_methods(dispatcher, registry=registry, pending_actions=pending)

    async def _noop(method: str, params: Any) -> None:  # pragma: no cover
        return None

    response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "action.describe",
            "params": {"name": "external.send"},
        },
        _noop,
    )
    assert response is not None
    result = response["result"]
    assert result["hardGate"] is True
    assert result["hardGateKind"] == "external_send"
    assert [slot["name"] for slot in result["inputs"]] == ["to", "body"]


@pytest.mark.asyncio
async def test_action_approve_runs_executor_with_resolved_gate() -> None:
    registry = ActionRegistry()
    recorded: list[Mapping[str, Any]] = []
    _register_test_actions(registry, recorded=recorded)
    pending = PendingActionStore()
    dispatcher = Dispatcher()
    register_action_methods(dispatcher, registry=registry, pending_actions=pending)

    staged = await pending.stage(
        action_name="external.send",
        inputs={"to": "alice@example.com", "body": "shipping update"},
        hard_gate_kind="external_send",
        preview="Email Alice",
        thread_id="thread_x",
        turn_id="turn_y",
    )

    async def _noop(method: str, params: Any) -> None:  # pragma: no cover
        return None

    response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "action.approve",
            "params": {"approvalId": staged.approval_id},
        },
        _noop,
    )
    assert response is not None
    result = response["result"]
    assert result["status"] == "approved"
    assert result["actionName"] == "external.send"
    assert "alice@example.com" in result["confirmation"]
    assert result["followup"] == {"deliveryId": "msg_42"}
    # The executor saw the inputs the matcher pulled out of the prompt.
    assert recorded == [{"to": "alice@example.com", "body": "shipping update"}]
    # And the staged row flipped to ``approved`` so a re-approve loses
    # the race.
    second = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "action.approve",
            "params": {"approvalId": staged.approval_id},
        },
        _noop,
    )
    assert second is not None
    assert "error" in second


@pytest.mark.asyncio
async def test_action_reject_skips_executor() -> None:
    registry = ActionRegistry()
    recorded: list[Mapping[str, Any]] = []
    _register_test_actions(registry, recorded=recorded)
    pending = PendingActionStore()
    dispatcher = Dispatcher()
    register_action_methods(dispatcher, registry=registry, pending_actions=pending)

    staged = await pending.stage(
        action_name="external.send",
        inputs={"to": "alice@example.com", "body": "no thanks"},
        hard_gate_kind="external_send",
        preview="Email Alice",
        thread_id="thread_x",
        turn_id="turn_y",
    )

    async def _noop(method: str, params: Any) -> None:  # pragma: no cover
        return None

    response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "action.reject",
            "params": {"approvalId": staged.approval_id},
        },
        _noop,
    )
    assert response is not None
    assert response["result"]["status"] == "rejected"
    assert recorded == []  # executor never ran


@pytest.mark.asyncio
async def test_action_approve_unknown_id_errors() -> None:
    registry = ActionRegistry()
    _register_test_actions(registry, recorded=[])
    pending = PendingActionStore()
    dispatcher = Dispatcher()
    register_action_methods(dispatcher, registry=registry, pending_actions=pending)

    async def _noop(method: str, params: Any) -> None:  # pragma: no cover
        return None

    response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "action.approve",
            "params": {"approvalId": "actappr_bogus"},
        },
        _noop,
    )
    assert response is not None
    assert "error" in response
