"""End-to-end plan-approval round-trip.

Drives the full wire shape that the renderer experiences — chat.send
(JSON-RPC) → run.approval_required notification → run.approve_plan
(JSON-RPC) → final state — and verifies every observable surface
(notifications, runs index, audit log on disk) reflects the decision.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from thalyn_brain.approval_rpc import register_approval_methods
from thalyn_brain.chat import register_chat_methods
from thalyn_brain.orchestration import Runner
from thalyn_brain.provider import AnthropicProvider, ProviderRegistry
from thalyn_brain.rpc import Dispatcher
from thalyn_brain.runs import RunsStore
from thalyn_brain.runs_rpc import register_runs_methods

from tests.provider._fake_sdk import factory_for, result_message, text_message


def _registry_with(provider: AnthropicProvider) -> ProviderRegistry:
    registry = ProviderRegistry()
    registry._providers["anthropic"] = provider
    return registry


def _build_dispatcher(
    registry: ProviderRegistry,
    tmp_path: Path,
) -> tuple[Dispatcher, RunsStore]:
    store = RunsStore(data_dir=tmp_path)
    runner = Runner(registry, runs_store=store, data_dir=tmp_path)
    dispatcher = Dispatcher()
    register_chat_methods(dispatcher, registry, runner=runner)
    register_approval_methods(dispatcher, runner)
    register_runs_methods(dispatcher, store)
    return dispatcher, store


def _capture() -> tuple[list[tuple[str, Any]], Any]:
    captured: list[tuple[str, Any]] = []

    async def notify(method: str, params: Any) -> None:
        captured.append((method, params))

    return captured, notify


def _read_audit(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


async def test_e2e_plan_approval_round_trip(tmp_path: Path) -> None:
    """Happy-path: plan, approval modal opens, user approves, run completes."""
    _fake, factory = factory_for(
        [
            text_message(
                '{"goal": "Hello", "steps": [{'
                '"description": "Wave back.",'
                '"rationale": "Friendly.",'
                '"estimated_tokens": 200'
                "}]}"
            ),
            result_message(),
            text_message("Hello, friend!"),
            result_message(total_cost_usd=0.0001),
        ]
    )
    provider = AnthropicProvider(client_factory=factory)
    registry = _registry_with(provider)
    store = RunsStore(data_dir=tmp_path)
    runner = Runner(registry, runs_store=store, data_dir=tmp_path)
    dispatcher = Dispatcher()
    register_chat_methods(dispatcher, registry, runner=runner)
    register_approval_methods(dispatcher, runner)
    register_runs_methods(dispatcher, store)

    captured, notify = _capture()

    # Step 1 — chat.send drives the planner and pauses at the gate.
    send_response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "chat.send",
            "params": {
                "sessionId": "sess",
                "providerId": "anthropic",
                "prompt": "Say hello.",
            },
        },
        notify,
    )
    assert send_response is not None
    send_result = send_response["result"]
    assert send_result["status"] == "awaiting_approval"
    run_id = send_result["runId"]
    assert send_result["plan"]["goal"] == "Hello"
    assert len(send_result["plan"]["nodes"]) == 1

    # The renderer would receive run:approval_required at this point.
    approval_required = [params for method, params in captured if method == "run.approval_required"]
    assert len(approval_required) == 1
    assert approval_required[0]["runId"] == run_id

    # Step 2 — user approves; the run resumes and completes.
    approve_response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "run.approve_plan",
            "params": {
                "runId": run_id,
                "providerId": "anthropic",
                "decision": "approve",
            },
        },
        notify,
    )
    assert approve_response is not None
    approve_result = approve_response["result"]
    assert approve_result["status"] == "completed"
    assert approve_result["finalResponse"] == "Hello, friend!"
    assert approve_result["plan"]["goal"] == "Hello"

    # Chat chunks streamed during the resumption.
    chat_chunks = [params for method, params in captured if method == "chat.chunk"]
    chunk_kinds = [params["chunk"]["kind"] for params in chat_chunks]
    assert "start" in chunk_kinds
    assert "text" in chunk_kinds
    assert chunk_kinds[-1] == "stop"

    # Run-lifecycle notifications cover the whole arc.
    statuses = [params["status"] for method, params in captured if method == "run.status"]
    assert "pending" in statuses
    assert "planning" in statuses
    assert "awaiting_approval" in statuses
    assert "running" in statuses
    assert statuses[-1] == "completed"

    # Step 3 — the runs index reflects the final state.
    list_response = await dispatcher.handle(
        {"jsonrpc": "2.0", "id": 3, "method": "runs.list"},
        notify,
    )
    assert list_response is not None
    runs = list_response["result"]["runs"]
    matching = next(r for r in runs if r["runId"] == run_id)
    assert matching["status"] == "completed"
    assert matching["finalResponse"] == "Hello, friend!"

    # Step 4 — the audit log captured the decision and lifecycle.
    log_path = tmp_path / "runs" / f"{run_id}.log"
    audit = _read_audit(log_path)
    kinds = [line["kind"] for line in audit]
    assert "status" in kinds
    assert "plan_update" in kinds
    assert "approval_required" in kinds
    assert "approval" in kinds
    approval_decision = next(line for line in audit if line["kind"] == "approval")["payload"][
        "decision"
    ]
    assert approval_decision == "approve"


async def test_e2e_plan_approval_reject_round_trip(tmp_path: Path) -> None:
    """A run the user rejects ends in killed; no respond traffic flows."""
    _fake, factory = factory_for([text_message('{"goal": "x", "steps": []}'), result_message()])
    provider = AnthropicProvider(client_factory=factory)
    registry = _registry_with(provider)
    dispatcher, store = _build_dispatcher(registry, tmp_path)

    captured, notify = _capture()

    send_response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "chat.send",
            "params": {
                "sessionId": "sess",
                "providerId": "anthropic",
                "prompt": "Reject me.",
            },
        },
        notify,
    )
    assert send_response is not None
    run_id = send_response["result"]["runId"]

    reject_response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "run.approve_plan",
            "params": {
                "runId": run_id,
                "providerId": "anthropic",
                "decision": "reject",
            },
        },
        notify,
    )
    assert reject_response is not None
    assert reject_response["result"]["status"] == "killed"

    # No chat.chunk events: respond never ran.
    chat_chunks = [method for method, _ in captured if method == "chat.chunk"]
    assert chat_chunks == []

    header = await store.get(run_id)
    assert header is not None
    assert header.status == "killed"


@pytest.mark.parametrize("decision", ["approve", "edit"])
async def test_resumes_emit_chat_chunks_only_after_approval(tmp_path: Path, decision: str) -> None:
    """Whichever resume-style decision the user picks, the chat
    chunks fire only after the gate clears."""
    _fake, factory = factory_for(
        [
            text_message('{"goal": "x", "steps": []}'),
            result_message(),
            text_message("ok."),
            result_message(),
        ]
    )
    provider = AnthropicProvider(client_factory=factory)
    registry = _registry_with(provider)
    dispatcher, _store = _build_dispatcher(registry, tmp_path)

    captured, notify = _capture()
    send_response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "chat.send",
            "params": {
                "sessionId": "sess",
                "providerId": "anthropic",
                "prompt": "Hello",
            },
        },
        notify,
    )
    assert send_response is not None
    run_id = send_response["result"]["runId"]

    # Right after chat.send returns, no chat.chunk events have fired.
    pre_approval_chunks = [m for m, _ in captured if m == "chat.chunk"]
    assert pre_approval_chunks == []

    edit_payload: dict[str, Any] = {}
    if decision == "edit":
        edit_payload["editedPlan"] = {
            "goal": "edited",
            "nodes": [
                {
                    "id": "step_1",
                    "order": 0,
                    "description": "Edited.",
                    "rationale": "Test.",
                    "estimatedCost": {},
                    "status": "pending",
                    "parentId": None,
                }
            ],
        }

    approve_response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "run.approve_plan",
            "params": {
                "runId": run_id,
                "providerId": "anthropic",
                "decision": decision,
                **edit_payload,
            },
        },
        notify,
    )
    assert approve_response is not None
    assert approve_response["result"]["status"] == "completed"

    post_approval_chunks = [m for m, _ in captured if m == "chat.chunk"]
    assert len(post_approval_chunks) > 0
