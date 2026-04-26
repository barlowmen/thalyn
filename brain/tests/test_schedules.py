"""Schedule store + JSON-RPC surface tests."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from thalyn_brain.provider import AnthropicProvider, ProviderRegistry
from thalyn_brain.rpc import Dispatcher
from thalyn_brain.schedules import (
    Schedule,
    SchedulerLoop,
    SchedulerLoopConfig,
    SchedulesStore,
    new_schedule_id,
    next_fire_ms,
)
from thalyn_brain.schedules_rpc import register_schedule_methods

from tests.provider._fake_sdk import factory_for, result_message, text_message


def _registry_with(provider: AnthropicProvider) -> ProviderRegistry:
    registry = ProviderRegistry()
    registry._providers["anthropic"] = provider
    return registry


def _schedule(**overrides: Any) -> Schedule:
    base: dict[str, Any] = {
        "schedule_id": "s_test",
        "project_id": None,
        "title": "Test schedule",
        "nl_input": "every weekday at 6 a.m.",
        "cron": "0 6 * * 1-5",
        "run_template": {"prompt": "Summarize.", "providerId": "anthropic"},
        "enabled": True,
        "next_run_at_ms": None,
        "last_run_at_ms": None,
        "last_run_id": None,
        "created_at_ms": 1000,
    }
    base.update(overrides)
    return Schedule(**base)


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


async def test_insert_and_get_round_trip(tmp_path: Path) -> None:
    store = SchedulesStore(data_dir=tmp_path)
    await store.insert(_schedule())
    fetched = await store.get("s_test")
    assert fetched is not None
    assert fetched.cron == "0 6 * * 1-5"
    assert fetched.run_template == {"prompt": "Summarize.", "providerId": "anthropic"}


async def test_list_orders_by_created_at_desc(tmp_path: Path) -> None:
    store = SchedulesStore(data_dir=tmp_path)
    await store.insert(_schedule(schedule_id="s_a", created_at_ms=100))
    await store.insert(_schedule(schedule_id="s_b", created_at_ms=200))
    rows = await store.list_all()
    assert [row.schedule_id for row in rows] == ["s_b", "s_a"]


async def test_delete_returns_true_only_when_row_existed(tmp_path: Path) -> None:
    store = SchedulesStore(data_dir=tmp_path)
    await store.insert(_schedule())
    assert await store.delete("s_test") is True
    assert await store.delete("s_test") is False


def test_next_fire_ms_returns_future_for_recurring_cron() -> None:
    now_ms = 1_700_000_000_000
    next_at = next_fire_ms("0 6 * * 1-5", now_ms=now_ms)
    assert next_at > now_ms


# ---------------------------------------------------------------------------
# JSON-RPC surface
# ---------------------------------------------------------------------------


async def test_create_with_explicit_cron(tmp_path: Path) -> None:
    _fake, factory = factory_for([])
    provider = AnthropicProvider(client_factory=factory)
    registry = _registry_with(provider)
    store = SchedulesStore(data_dir=tmp_path)
    dispatcher = Dispatcher()
    register_schedule_methods(dispatcher, store, registry)

    async def notify(method: str, params: Any) -> None:
        del method, params

    response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "schedules.create",
            "params": {
                "title": "Daily summary",
                "cron": "0 6 * * *",
                "runTemplate": {"prompt": "Summarize the day", "providerId": "anthropic"},
            },
        },
        notify,
    )
    assert response is not None
    schedule = response["result"]["schedule"]
    assert schedule["cron"] == "0 6 * * *"
    assert schedule["title"] == "Daily summary"
    assert schedule["nextRunAtMs"] is not None
    rows = await store.list_all()
    assert len(rows) == 1


async def test_create_translates_nl_when_no_cron_supplied(tmp_path: Path) -> None:
    _fake, factory = factory_for(
        [
            text_message('{"cron": "0 6 * * 1-5", "explanation": "weekdays at 6"}'),
            result_message(),
        ]
    )
    provider = AnthropicProvider(client_factory=factory)
    registry = _registry_with(provider)
    store = SchedulesStore(data_dir=tmp_path)
    dispatcher = Dispatcher()
    register_schedule_methods(dispatcher, store, registry)

    async def notify(method: str, params: Any) -> None:
        del method, params

    response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "schedules.create",
            "params": {
                "title": "Weekday summary",
                "nlInput": "every weekday at 6 a.m.",
                "runTemplate": {"prompt": "Summarize.", "providerId": "anthropic"},
            },
        },
        notify,
    )
    assert response is not None
    schedule = response["result"]["schedule"]
    assert schedule["cron"] == "0 6 * * 1-5"
    assert schedule["nlInput"] == "every weekday at 6 a.m."


@pytest.mark.parametrize(
    "params",
    [
        {"title": "x", "cron": "garbage", "runTemplate": {"prompt": "p"}},
        {"title": "x", "runTemplate": {"prompt": "p"}},  # neither cron nor nlInput
        {"title": "x", "cron": "0 6 * * *", "runTemplate": {"prompt": ""}},
    ],
)
async def test_create_rejects_invalid_input(tmp_path: Path, params: dict[str, Any]) -> None:
    _fake, factory = factory_for([])
    provider = AnthropicProvider(client_factory=factory)
    registry = _registry_with(provider)
    store = SchedulesStore(data_dir=tmp_path)
    dispatcher = Dispatcher()
    register_schedule_methods(dispatcher, store, registry)

    async def notify(method: str, params: Any) -> None:
        del method, params

    response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "schedules.create",
            "params": params,
        },
        notify,
    )
    assert response is not None
    assert response["error"]["code"] == -32602


async def test_delete_removes_a_schedule(tmp_path: Path) -> None:
    _fake, factory = factory_for([])
    provider = AnthropicProvider(client_factory=factory)
    registry = _registry_with(provider)
    store = SchedulesStore(data_dir=tmp_path)
    await store.insert(_schedule(schedule_id=new_schedule_id()))
    dispatcher = Dispatcher()
    register_schedule_methods(dispatcher, store, registry)

    async def notify(method: str, params: Any) -> None:
        del method, params

    rows = await store.list_all()
    schedule_id = rows[0].schedule_id

    response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "schedules.delete",
            "params": {"scheduleId": schedule_id},
        },
        notify,
    )
    assert response is not None
    assert response["result"]["deleted"] is True
    assert await store.list_all() == []


# ---------------------------------------------------------------------------
# SchedulerLoop
# ---------------------------------------------------------------------------


async def test_scheduler_loop_dispatches_due_schedules(tmp_path: Path) -> None:
    """A schedule whose ``next_run_at_ms`` has elapsed fires once
    on the next tick; the dispatch callback receives the schedule
    and the next-run timestamp advances forward."""
    store = SchedulesStore(data_dir=tmp_path)
    past = 1_000_000  # well in the past
    await store.insert(
        _schedule(
            schedule_id="s_due",
            cron="*/5 * * * *",
            next_run_at_ms=past,
        )
    )

    dispatched: list[str] = []

    async def dispatch(schedule: Schedule) -> str:
        dispatched.append(schedule.schedule_id)
        return f"r_{schedule.schedule_id}"

    loop = SchedulerLoop(store, dispatch, config=SchedulerLoopConfig(poll_seconds=0.05))
    loop.start()
    # Give the loop a couple of ticks.
    await asyncio.sleep(0.2)
    await loop.stop()

    assert dispatched == ["s_due"]
    refreshed = await store.get("s_due")
    assert refreshed is not None
    assert refreshed.last_run_id == "r_s_due"
    assert refreshed.last_run_at_ms is not None
    assert refreshed.next_run_at_ms is not None and refreshed.next_run_at_ms > past


async def test_scheduler_loop_skips_disabled_schedules(tmp_path: Path) -> None:
    store = SchedulesStore(data_dir=tmp_path)
    await store.insert(
        _schedule(
            schedule_id="s_off",
            cron="*/5 * * * *",
            next_run_at_ms=1_000_000,
            enabled=False,
        )
    )

    dispatched: list[str] = []

    async def dispatch(schedule: Schedule) -> str:
        dispatched.append(schedule.schedule_id)
        return None  # type: ignore[return-value]

    loop = SchedulerLoop(store, dispatch, config=SchedulerLoopConfig(poll_seconds=0.05))
    loop.start()
    await asyncio.sleep(0.15)
    await loop.stop()

    assert dispatched == []
