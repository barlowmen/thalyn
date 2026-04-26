"""End-to-end sub-agent lifecycle across the JSON-RPC surface.

Walks the wire path the renderer drives — chat.send pauses at
approval, run.approve_plan resumes with the delegated step in place,
the child run completes under its own runId, runs.tree returns the
hierarchy used by the inspector, and runs.get returns the headers
the take-over flow snapshots into a fresh chat session.
"""

from __future__ import annotations

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


def _build_dispatcher(
    registry: ProviderRegistry,
    tmp_path: Path,
) -> tuple[Dispatcher, RunsStore, Runner]:
    store = RunsStore(data_dir=tmp_path)
    runner = Runner(registry, runs_store=store, data_dir=tmp_path)
    dispatcher = Dispatcher()
    register_chat_methods(dispatcher, registry, runner=runner)
    register_approval_methods(dispatcher, runner)
    register_runs_methods(dispatcher, store, runner=runner)
    return dispatcher, store, runner


def _delegated_plan() -> str:
    return (
        '{"goal": "investigate prior art",'
        ' "steps": ['
        '   {"description": "Audit existing call sites.",'
        '    "rationale": "Need a full picture.",'
        '    "estimated_tokens": 800,'
        '    "subagent_kind": "research"}'
        "]}"
    )


async def test_e2e_subagent_spawn_observe_takeover_round_trip(
    tmp_path: Path,
) -> None:
    _fake, factory = factory_for(
        [
            # Parent plan: one delegated step.
            text_message(_delegated_plan()),
            result_message(),
            # Child plan: empty (fallback single-step).
            text_message('{"goal": "x", "steps": []}'),
            result_message(),
            # Child respond turn.
            text_message("Investigated the call sites."),
            result_message(total_cost_usd=0.0001),
            # Parent respond turn.
            text_message("Here's what the sub-agent found."),
            result_message(total_cost_usd=0.0002),
        ]
    )
    provider = AnthropicProvider(client_factory=factory)
    registry = _registry_with(provider)
    dispatcher, store, _runner = _build_dispatcher(registry, tmp_path)

    captured, notify = _capture()

    # 1. chat.send drives the planner and pauses at the gate.
    send_response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "chat.send",
            "params": {
                "sessionId": "sess",
                "providerId": "anthropic",
                "prompt": "Investigate.",
            },
        },
        notify,
    )
    assert send_response is not None
    send_result = send_response["result"]
    root_run_id = send_result["runId"]
    assert send_result["status"] == RunStatus.AWAITING_APPROVAL.value

    # 2. Approve the plan; the spawn happens during the resumed graph.
    approve_response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "run.approve_plan",
            "params": {
                "runId": root_run_id,
                "providerId": "anthropic",
                "decision": "approve",
            },
        },
        notify,
    )
    assert approve_response is not None
    assert approve_response["result"]["status"] == RunStatus.COMPLETED.value
    assert approve_response["result"]["finalResponse"] == "Here's what the sub-agent found."

    # 3. The child fired its own status notifications under its own runId.
    child_run_ids = {
        params["runId"]
        for method, params in captured
        if method == "run.status" and params["runId"] != root_run_id
    }
    assert len(child_run_ids) == 1
    child_run_id = next(iter(child_run_ids))

    child_status_payloads = [
        params
        for method, params in captured
        if method == "run.status" and params["runId"] == child_run_id
    ]
    # The child's first status carries parentRunId so the renderer can
    # route it to the sub-agent tree without a second lookup.
    assert child_status_payloads[0]["parentRunId"] == root_run_id
    assert child_status_payloads[0]["status"] == RunStatus.PENDING.value
    assert child_status_payloads[-1]["status"] == RunStatus.COMPLETED.value

    # 4. runs.tree returns the parent + child hierarchy the inspector uses.
    tree_response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "runs.tree",
            "params": {"runId": root_run_id},
        },
        notify,
    )
    assert tree_response is not None
    tree = tree_response["result"]
    assert tree["runId"] == root_run_id
    assert tree["status"] == RunStatus.COMPLETED.value
    child_nodes = tree["children"]
    assert len(child_nodes) == 1
    assert child_nodes[0]["runId"] == child_run_id
    assert child_nodes[0]["parentRunId"] == root_run_id
    assert child_nodes[0]["status"] == RunStatus.COMPLETED.value
    assert child_nodes[0]["title"] == "Audit existing call sites."

    # 5. The renderer's take-over flow snapshots from runs.get; verify the
    #    headers carry everything the system-prompt builder needs.
    child_get = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "runs.get",
            "params": {"runId": child_run_id},
        },
        notify,
    )
    assert child_get is not None
    snapshot = child_get["result"]
    assert snapshot["title"] == "Audit existing call sites."
    assert snapshot["plan"] is not None
    assert snapshot["finalResponse"] == "Investigated the call sites."

    # The runs index reflects the parent / child relationship.
    headers = await store.list_runs()
    by_id = {h.run_id: h for h in headers}
    assert by_id[root_run_id].parent_run_id is None
    assert by_id[child_run_id].parent_run_id == root_run_id


async def test_e2e_kill_run_propagates_through_index_and_notifications(
    tmp_path: Path,
) -> None:
    """``runs.kill`` over the wire flips a paused run to killed and
    fires the lifecycle notification with the paused run's parent
    link, exactly as a graceful completion would."""
    _fake, factory = factory_for(
        [
            text_message('{"goal": "x", "steps": []}'),
            result_message(),
        ]
    )
    provider = AnthropicProvider(client_factory=factory)
    registry = _registry_with(provider)
    dispatcher, store, _runner = _build_dispatcher(registry, tmp_path)

    _, send_notify = _capture()
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
        send_notify,
    )
    assert send_response is not None
    run_id = send_response["result"]["runId"]

    captured, notify = _capture()
    kill_response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "runs.kill",
            "params": {"runId": run_id},
        },
        notify,
    )
    assert kill_response is not None
    assert kill_response["result"]["runId"] == run_id
    assert kill_response["result"]["status"] == RunStatus.KILLED.value

    statuses = [
        params["status"]
        for method, params in captured
        if method == "run.status" and params["runId"] == run_id
    ]
    assert statuses == [RunStatus.KILLED.value]

    header = await store.get(run_id)
    assert header is not None
    assert header.status == RunStatus.KILLED.value
    assert header.completed_at_ms is not None
