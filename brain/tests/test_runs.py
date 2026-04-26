"""Tests for the runs index store and JSON-RPC bindings."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from thalyn_brain.orchestration import Runner
from thalyn_brain.orchestration.state import RunStatus
from thalyn_brain.provider import AnthropicProvider, ProviderRegistry
from thalyn_brain.rpc import Dispatcher
from thalyn_brain.runs import RunHeader, RunsStore, RunUpdate
from thalyn_brain.runs_rpc import register_runs_methods

from tests.provider._fake_sdk import factory_for, result_message, text_message


def _registry_with(provider: AnthropicProvider) -> ProviderRegistry:
    registry = ProviderRegistry()
    registry._providers["anthropic"] = provider
    return registry


def _header(**overrides: Any) -> RunHeader:
    base: dict[str, Any] = {
        "run_id": "r_1",
        "project_id": None,
        "parent_run_id": None,
        "status": RunStatus.RUNNING.value,
        "title": "Hello",
        "provider_id": "anthropic",
        "started_at_ms": 1000,
        "completed_at_ms": None,
        "drift_score": 0.0,
        "final_response": "",
        "plan": None,
    }
    base.update(overrides)
    return RunHeader(**base)


async def test_insert_and_get_round_trip(tmp_path: Path) -> None:
    store = RunsStore(data_dir=tmp_path)
    await store.insert(_header())
    fetched = await store.get("r_1")
    assert fetched is not None
    assert fetched.run_id == "r_1"
    assert fetched.title == "Hello"
    assert fetched.status == RunStatus.RUNNING.value


async def test_get_unknown_returns_none(tmp_path: Path) -> None:
    store = RunsStore(data_dir=tmp_path)
    assert await store.get("missing") is None


async def test_update_changes_status_and_completion(tmp_path: Path) -> None:
    store = RunsStore(data_dir=tmp_path)
    await store.insert(_header())
    await store.update(
        "r_1",
        RunUpdate(
            status=RunStatus.COMPLETED.value,
            completed_at_ms=2000,
            final_response="ok.",
        ).with_plan({"goal": "x", "nodes": []}),
    )
    fetched = await store.get("r_1")
    assert fetched is not None
    assert fetched.status == RunStatus.COMPLETED.value
    assert fetched.completed_at_ms == 2000
    assert fetched.final_response == "ok."
    assert fetched.plan == {"goal": "x", "nodes": []}


async def test_list_orders_by_started_desc_with_limit(tmp_path: Path) -> None:
    store = RunsStore(data_dir=tmp_path)
    await store.insert(_header(run_id="r_a", started_at_ms=100, title="a"))
    await store.insert(_header(run_id="r_b", started_at_ms=200, title="b"))
    await store.insert(_header(run_id="r_c", started_at_ms=300, title="c"))
    rows = await store.list_runs(limit=2)
    assert [row.run_id for row in rows] == ["r_c", "r_b"]


async def test_list_filters_by_status(tmp_path: Path) -> None:
    store = RunsStore(data_dir=tmp_path)
    await store.insert(_header(run_id="r_completed", status=RunStatus.COMPLETED.value))
    await store.insert(_header(run_id="r_running", status=RunStatus.RUNNING.value))
    completed = await store.list_runs(statuses=[RunStatus.COMPLETED.value])
    assert [row.run_id for row in completed] == ["r_completed"]


async def test_list_unfinished_returns_in_flight_runs(tmp_path: Path) -> None:
    store = RunsStore(data_dir=tmp_path)
    await store.insert(_header(run_id="r_done", status=RunStatus.COMPLETED.value))
    await store.insert(_header(run_id="r_running", status=RunStatus.RUNNING.value))
    await store.insert(_header(run_id="r_pending", status=RunStatus.PENDING.value))
    rows = await store.list_unfinished()
    ids = {row.run_id for row in rows}
    assert ids == {"r_running", "r_pending"}


async def test_runner_writes_header_for_each_run(tmp_path: Path) -> None:
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
    store = RunsStore(data_dir=tmp_path)
    runner = Runner(registry, runs_store=store, data_dir=tmp_path)

    async def notify(method: str, params: Any) -> None:
        del method, params

    paused = await runner.run(
        session_id="s",
        provider_id="anthropic",
        prompt="Multi\nline\nprompt",
        notify=notify,
    )
    result = await runner.approve_plan(
        run_id=paused.run_id,
        provider_id="anthropic",
        decision="approve",
        notify=notify,
    )
    assert result is not None

    header = await store.get(result.run_id)
    assert header is not None
    assert header.status == RunStatus.COMPLETED.value
    assert header.title == "Multi"
    assert header.completed_at_ms is not None
    assert header.plan is not None


async def test_runs_rpc_list_and_get(tmp_path: Path) -> None:
    store = RunsStore(data_dir=tmp_path)
    await store.insert(_header(run_id="r_one"))
    await store.insert(_header(run_id="r_two", started_at_ms=2000))

    dispatcher = Dispatcher()
    register_runs_methods(dispatcher, store)

    async def notify(method: str, params: Any) -> None:
        del method, params

    list_response = await dispatcher.handle(
        {"jsonrpc": "2.0", "id": 1, "method": "runs.list"},
        notify,
    )
    assert list_response is not None
    runs = list_response["result"]["runs"]
    assert {r["runId"] for r in runs} == {"r_one", "r_two"}

    get_response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "runs.get",
            "params": {"runId": "r_one"},
        },
        notify,
    )
    assert get_response is not None
    assert get_response["result"]["runId"] == "r_one"


async def test_runs_rpc_get_missing_returns_null_result(tmp_path: Path) -> None:
    store = RunsStore(data_dir=tmp_path)
    dispatcher = Dispatcher()
    register_runs_methods(dispatcher, store)

    async def notify(method: str, params: Any) -> None:
        del method, params

    response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "runs.get",
            "params": {"runId": "missing"},
        },
        notify,
    )
    assert response is not None
    assert response["result"] is None


@pytest.mark.parametrize(
    "params",
    [
        {"limit": 0},
        {"limit": -1},
        {"limit": "ten"},
        {"projectId": 42},
        {"statuses": "running"},
        {"statuses": [1, 2]},
    ],
)
async def test_runs_rpc_list_validates_params(tmp_path: Path, params: dict[str, Any]) -> None:
    store = RunsStore(data_dir=tmp_path)
    dispatcher = Dispatcher()
    register_runs_methods(dispatcher, store)

    async def notify(method: str, params: Any) -> None:
        del method, params

    response = await dispatcher.handle(
        {"jsonrpc": "2.0", "id": 1, "method": "runs.list", "params": params},
        notify,
    )
    assert response is not None
    assert response["error"]["code"] == -32602
