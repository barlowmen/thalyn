"""End-to-end drift-gate flow over the JSON-RPC surface.

Walks the path the renderer drives — chat.send pauses at the
plan-approval interrupt, run.approve_plan resumes, the critic
fires when budget consumption crosses 25 %, a high drift verdict
halts the run with `gateKind: "drift"`, and the runs index reflects
the drift score and KILLED status.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from thalyn_brain.approval_rpc import register_approval_methods
from thalyn_brain.chat import register_chat_methods
from thalyn_brain.orchestration import Runner
from thalyn_brain.orchestration.state import RunStatus
from thalyn_brain.provider import AnthropicProvider, ProviderRegistry
from thalyn_brain.rpc import Dispatcher
from thalyn_brain.runs import RunsStore
from thalyn_brain.runs_rpc import register_runs_methods

from tests.provider._fake_sdk import factory_for, result_message, text_message


def _registry_with(provider: AnthropicProvider) -> ProviderRegistry:
    registry = ProviderRegistry()
    registry._providers["anthropic"] = provider
    return registry


def _capture() -> tuple[list[tuple[str, Any]], Any]:
    captured: list[tuple[str, Any]] = []

    async def notify(method: str, params: Any) -> None:
        captured.append((method, params))

    return captured, notify


def _build(registry: ProviderRegistry, tmp_path: Path) -> tuple[Dispatcher, RunsStore]:
    store = RunsStore(data_dir=tmp_path)
    runner = Runner(registry, runs_store=store, data_dir=tmp_path)
    dispatcher = Dispatcher()
    register_chat_methods(dispatcher, registry, runner=runner)
    register_approval_methods(dispatcher, runner)
    register_runs_methods(dispatcher, store, runner=runner)
    return dispatcher, store


def _read_audit(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


async def test_e2e_synthetic_drift_triggers_pause(tmp_path: Path) -> None:
    """A high-drift critic verdict halts the run with a drift gate
    and records the verdict in the audit log."""
    _fake, factory = factory_for(
        [
            # Plan turn — empty steps so the heuristic stays at 0 and
            # the LLM verdict drives the gate.
            text_message('{"goal": "x", "steps": []}'),
            result_message(),
            # Critic turn — high drift.
            text_message(
                '{"drift_score": 0.92,'
                ' "on_track": false,'
                ' "reason": "Sub-agent has wandered into unrelated repos."}'
            ),
            result_message(),
        ]
    )
    provider = AnthropicProvider(client_factory=factory)
    registry = _registry_with(provider)
    dispatcher, _store = _build(registry, tmp_path)

    captured, notify = _capture()

    # 1. chat.send with a budget so the critic crosses a checkpoint.
    send_response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "chat.send",
            "params": {
                "sessionId": "sess",
                "providerId": "anthropic",
                "prompt": "Investigate auth.",
                "budget": {"maxIterations": 12},
            },
        },
        notify,
    )
    assert send_response is not None
    run_id = send_response["result"]["runId"]
    assert send_response["result"]["status"] == RunStatus.AWAITING_APPROVAL.value

    # 2. Approve the plan; the critic fires during the resumed graph
    #    and the high drift score halts the run.
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
    assert approve_response["result"]["status"] == RunStatus.KILLED.value

    # 3. The drift gate notification fired with the right shape.
    drift_gates = [
        params
        for method, params in captured
        if method == "run.approval_required" and params.get("gateKind") == "drift"
    ]
    assert len(drift_gates) == 1
    gate = drift_gates[0]
    assert gate["runId"] == run_id
    assert gate["driftScore"] == 0.92
    assert "wandered" in gate["reason"]

    # 4. The runs index records the killed status and the drift score.
    get_response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "runs.get",
            "params": {"runId": run_id},
        },
        notify,
    )
    assert get_response is not None
    header = get_response["result"]
    assert header["status"] == RunStatus.KILLED.value
    assert header["driftScore"] == 0.92
    assert header["budget"]["maxIterations"] == 12
    assert header["budgetConsumed"]["iterations"] >= 1

    # 5. The audit log preserves the critic verdict for replay /
    #    inspection. Two drift_check entries land per check (one for
    #    the budget bookkeeping at node entry, one for the critic's
    #    call), so just confirm the critic's lands with its score.
    log_path = tmp_path / "runs" / f"{run_id}.log"
    audit = _read_audit(log_path)
    critic_entries = [
        line
        for line in audit
        if line["kind"] == "action_log"
        and line["payload"]["entry"]["kind"] == "drift_check"
        and line["payload"]["entry"]["payload"].get("step") == "critic"
    ]
    assert critic_entries, "expected the critic verdict to land in the audit log"
    assert critic_entries[0]["payload"]["entry"]["payload"]["driftScore"] == 0.92


async def test_e2e_no_budget_no_drift_gate(tmp_path: Path) -> None:
    """A run without a budget never triggers the critic — even if the
    next FakeClient turn would have returned a high drift verdict, the
    critic node never asks for it."""
    _fake, factory = factory_for(
        [
            text_message('{"goal": "x", "steps": []}'),
            result_message(),
            text_message("hello back."),
            result_message(),
        ]
    )
    provider = AnthropicProvider(client_factory=factory)
    registry = _registry_with(provider)
    dispatcher, _store = _build(registry, tmp_path)

    captured, notify = _capture()
    send_response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "chat.send",
            "params": {
                "sessionId": "sess",
                "providerId": "anthropic",
                "prompt": "Hi",
            },
        },
        notify,
    )
    assert send_response is not None
    run_id = send_response["result"]["runId"]

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
    assert approve_response["result"]["status"] == RunStatus.COMPLETED.value

    drift_gates = [
        params
        for method, params in captured
        if method == "run.approval_required" and params.get("gateKind") == "drift"
    ]
    assert drift_gates == []
