"""End-to-end graph integration tests.

These exercise the compiled graph against a real SqliteSaver, the
full Notifier wire surface, and the runs index — the most
representative coverage of the v0.4 orchestration loop short of an
actual API call.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from thalyn_brain.orchestration import Runner
from thalyn_brain.orchestration.state import RunStatus
from thalyn_brain.orchestration.storage import run_db_path
from thalyn_brain.provider import AnthropicProvider, ProviderRegistry
from thalyn_brain.runs import RunsStore

from tests.provider._fake_sdk import (
    factory_for,
    result_message,
    text_message,
    tool_call_message,
    tool_result_message,
)


def _registry_with(provider: AnthropicProvider) -> ProviderRegistry:
    registry = ProviderRegistry()
    registry._providers["anthropic"] = provider
    return registry


def _captured() -> tuple[list[tuple[str, Any]], Any]:
    captured: list[tuple[str, Any]] = []

    async def notify(method: str, params: Any) -> None:
        captured.append((method, params))

    return captured, notify


async def test_graph_run_persists_per_run_db_and_emits_lifecycle(
    tmp_path: Path,
) -> None:
    _fake, factory = factory_for(
        [
            text_message(
                '{"goal": "list files", '
                '"steps": [{"description": "Read directory.", "rationale": "Need contents."}]}'
            ),
            result_message(),
            text_message("Files: a, b, c."),
            result_message(total_cost_usd=0.0003),
        ]
    )
    provider = AnthropicProvider(client_factory=factory)
    registry = _registry_with(provider)
    store = RunsStore(data_dir=tmp_path)
    runner = Runner(registry, runs_store=store, data_dir=tmp_path)

    captured, notify = _captured()
    paused = await runner.run(
        session_id="sess",
        provider_id="anthropic",
        prompt="List the repo contents",
        notify=notify,
    )

    # Plan-approval interrupt fires before execute; drive through the
    # rest of the graph by approving the plan.
    assert paused.status == RunStatus.AWAITING_APPROVAL.value
    result = await runner.approve_plan(
        run_id=paused.run_id,
        provider_id="anthropic",
        decision="approve",
        notify=notify,
    )
    assert result is not None
    assert result.status == RunStatus.COMPLETED.value

    methods = [method for method, _ in captured]
    assert "run.status" in methods
    assert "run.plan_update" in methods
    assert "run.action_log" in methods
    assert "chat.chunk" in methods

    statuses = [params["status"] for method, params in captured if method == "run.status"]
    assert statuses[0] == RunStatus.PENDING.value
    assert RunStatus.PLANNING.value in statuses
    assert RunStatus.AWAITING_APPROVAL.value in statuses
    assert RunStatus.RUNNING.value in statuses
    assert statuses[-1] == RunStatus.COMPLETED.value

    plan_update = next(params for method, params in captured if method == "run.plan_update")
    assert plan_update["plan"]["goal"] == "list files"

    db = run_db_path(result.run_id, data_dir=tmp_path)
    assert db.exists()
    assert db.stat().st_size > 0

    header = await store.get(result.run_id)
    assert header is not None
    assert header.status == RunStatus.COMPLETED.value
    assert header.plan is not None
    assert header.plan["goal"] == "list files"


async def test_action_log_records_node_transitions_and_decisions(
    tmp_path: Path,
) -> None:
    _fake, factory = factory_for(
        [
            text_message('{"goal": "ls", "steps": []}'),
            result_message(),
            tool_call_message(call_id="t_1", name="Bash", input_={"command": "ls"}),
            tool_result_message(call_id="t_1", output="a\nb\n"),
            text_message("done."),
            result_message(),
        ]
    )
    provider = AnthropicProvider(client_factory=factory)
    registry = _registry_with(provider)
    store = RunsStore(data_dir=tmp_path)
    runner = Runner(registry, runs_store=store, data_dir=tmp_path)

    captured, notify = _captured()
    paused = await runner.run(
        session_id="sess",
        provider_id="anthropic",
        prompt="ls files",
        notify=notify,
    )
    await runner.approve_plan(
        run_id=paused.run_id,
        provider_id="anthropic",
        decision="approve",
        notify=notify,
    )

    action_payloads = [
        params["entry"]["payload"] for method, params in captured if method == "run.action_log"
    ]
    # Plan node lays down a "decision" entry; execute and critic
    # both lay down "node_transition" entries with from/to fields.
    assert any(p.get("step") == "plan" for p in action_payloads)
    assert any({"from", "to"} <= set(p.keys()) for p in action_payloads)


async def test_resume_replays_graph_from_checkpoint(tmp_path: Path) -> None:
    """Drive a run, approve the plan, then resume from the checkpoint
    and confirm the runs-index header is left in COMPLETED state.

    The fake SDK has its messages drained on the original run; resuming
    against the LangGraph checkpoint replays state but doesn't replay
    the SDK calls (LangGraph caches node outputs). So we don't need a
    second batch of SDK messages.
    """
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

    _, notify = _captured()
    paused = await runner.run(
        session_id="s",
        provider_id="anthropic",
        prompt="Hello",
        notify=notify,
    )
    assert paused.status == RunStatus.AWAITING_APPROVAL.value
    first = await runner.approve_plan(
        run_id=paused.run_id,
        provider_id="anthropic",
        decision="approve",
        notify=notify,
    )
    assert first is not None
    assert first.status == RunStatus.COMPLETED.value

    second = await runner.resume(
        run_id=first.run_id,
        provider_id="anthropic",
        notify=notify,
    )
    # Resuming a completed run with no further work is a no-op
    # against the checkpoint; LangGraph returns the cached final
    # state, which keeps the run COMPLETED.
    assert second is not None
    assert second.status == RunStatus.COMPLETED.value


@pytest.mark.parametrize("provider_id", ["openai_compat", "llama_cpp", "mlx"])
async def test_runner_rejects_placeholder_providers(tmp_path: Path, provider_id: str) -> None:
    registry = ProviderRegistry()
    runner = Runner(registry, data_dir=tmp_path)

    _, notify = _captured()
    # Placeholder providers raise ProviderNotImplementedError when
    # stream_chat is iterated; the runner surfaces that error up.
    with pytest.raises(Exception):
        await runner.run(
            session_id="s",
            provider_id=provider_id,
            prompt="Hello",
            notify=notify,
        )
