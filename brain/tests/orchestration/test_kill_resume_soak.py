"""Randomized kill-and-resume soak test.

Drives N runs, each one paused at a random point in the
plan-approval flow, dropped, then resumed in a fresh Runner.
Asserts that every run lands in a consistent terminal or
awaiting-approval state — the data model survives every drop
point we can simulate without forking the process.

Three drop points are exercised: drop after the initial chat.send
returns (run is at the plan-approval gate); drop just after the
user approved (graph state has plan + status=running, ready to
finish); drop after completion (no-op resume should leave
the run in `completed`).
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import Any

import pytest
from thalyn_brain.orchestration import Runner
from thalyn_brain.orchestration.resume import resume_unfinished_runs
from thalyn_brain.orchestration.state import RunStatus
from thalyn_brain.provider import AnthropicProvider, ProviderRegistry
from thalyn_brain.runs import RunsStore

from tests.provider._fake_sdk import factory_for, result_message, text_message


def _registry_with(provider: AnthropicProvider) -> ProviderRegistry:
    registry = ProviderRegistry()
    registry._providers["anthropic"] = provider
    return registry


def _silent() -> Any:
    async def notify(method: str, params: Any) -> None:
        del method, params

    return notify


def _plan_messages() -> list[Any]:
    return [
        text_message('{"goal": "x", "steps": []}'),
        result_message(),
    ]


def _respond_messages() -> list[Any]:
    return [
        text_message("done."),
        result_message(),
    ]


@pytest.mark.parametrize("seed", list(range(8)))
async def test_random_kill_point_survives_resume(tmp_path: Path, seed: int) -> None:
    """For each randomized seed, kill the process at one of three
    drop points and confirm the runs index reflects the right
    terminal / pending state after the new Runner takes over."""
    rng = random.Random(seed)
    drop_point = rng.choice(["pre_approve", "post_approve", "post_complete"])

    # Seed both runners with all the messages they could need;
    # which messages each consumes depends on where we drop.
    messages_1 = _plan_messages() + _respond_messages()
    messages_2 = _plan_messages() + _respond_messages()

    fake_1, factory_1 = factory_for(messages_1)
    provider_1 = AnthropicProvider(client_factory=factory_1)
    registry_1 = _registry_with(provider_1)

    store = RunsStore(data_dir=tmp_path)
    runner_1 = Runner(registry_1, runs_store=store, data_dir=tmp_path)

    paused = await runner_1.run(
        session_id=f"sess_{seed}",
        provider_id="anthropic",
        prompt="Hello",
        notify=_silent(),
    )
    run_id = paused.run_id
    assert paused.status == RunStatus.AWAITING_APPROVAL.value

    if drop_point == "pre_approve":
        # Drop the runner before approving; the new runner should
        # find the run still parked at awaiting_approval.
        del runner_1, fake_1
        store_2 = RunsStore(data_dir=tmp_path)
        _, factory_2 = factory_for(messages_2)
        provider_2 = AnthropicProvider(client_factory=factory_2)
        registry_2 = _registry_with(provider_2)
        runner_2 = Runner(registry_2, runs_store=store_2, data_dir=tmp_path)
        await resume_unfinished_runs(store_2, runner_2)
        header = await store_2.get(run_id)
        assert header is not None
        assert header.status == RunStatus.AWAITING_APPROVAL.value
        return

    if drop_point == "post_approve":
        # Drop the runner mid-approve: a new runner picks up via
        # approve_plan, drives respond, the run completes.
        del runner_1, fake_1
        store_2 = RunsStore(data_dir=tmp_path)
        _, factory_2 = factory_for(_respond_messages())
        provider_2 = AnthropicProvider(client_factory=factory_2)
        registry_2 = _registry_with(provider_2)
        runner_2 = Runner(registry_2, runs_store=store_2, data_dir=tmp_path)
        finished = await runner_2.approve_plan(
            run_id=run_id,
            provider_id="anthropic",
            decision="approve",
            notify=_silent(),
        )
        assert finished is not None
        assert finished.status == RunStatus.COMPLETED.value
        return

    # drop_point == "post_complete"
    finished = await runner_1.approve_plan(
        run_id=run_id,
        provider_id="anthropic",
        decision="approve",
        notify=_silent(),
    )
    assert finished is not None
    assert finished.status == RunStatus.COMPLETED.value
    del runner_1, fake_1
    # New runner — resume_unfinished_runs should be a no-op
    # because the run is already terminal.
    store_2 = RunsStore(data_dir=tmp_path)
    _, factory_2 = factory_for(messages_2)
    provider_2 = AnthropicProvider(client_factory=factory_2)
    registry_2 = _registry_with(provider_2)
    runner_2 = Runner(registry_2, runs_store=store_2, data_dir=tmp_path)
    touched = await resume_unfinished_runs(store_2, runner_2)
    assert run_id not in touched
    header = await store_2.get(run_id)
    assert header is not None
    assert header.status == RunStatus.COMPLETED.value
