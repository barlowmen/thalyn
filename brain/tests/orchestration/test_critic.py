"""Critic-agent invocation tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from thalyn_brain.orchestration import Runner
from thalyn_brain.orchestration.budget import Budget
from thalyn_brain.orchestration.critic import (
    CriticReport,
    crossed_thresholds,
    run_critic_checkpoint,
)
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


# ---------------------------------------------------------------------------
# crossed_thresholds — pure function
# ---------------------------------------------------------------------------


def test_crossed_thresholds_returns_empty_when_budget_unset() -> None:
    assert crossed_thresholds({"iterations": 50}, None, already_hit=[]) == []
    assert crossed_thresholds({"iterations": 50}, {}, already_hit=[]) == []


def test_crossed_thresholds_returns_only_new_crossings() -> None:
    budget = {"maxIterations": 4}
    consumed_at_50 = {"iterations": 2}
    assert crossed_thresholds(consumed_at_50, budget, already_hit=[]) == [
        "25%",
        "50%",
    ]
    assert crossed_thresholds(consumed_at_50, budget, already_hit=["25%"]) == ["50%"]
    assert crossed_thresholds(consumed_at_50, budget, already_hit=["25%", "50%"]) == []


def test_crossed_thresholds_uses_max_dimension() -> None:
    budget = {"maxIterations": 4, "maxTokens": 1000}
    # iterations at 50 %, tokens at 80 % → 75 % cutoff fires.
    consumed = {"iterations": 2, "tokensUsed": 800}
    assert "75%" in crossed_thresholds(consumed, budget, already_hit=[])


# ---------------------------------------------------------------------------
# run_critic_checkpoint — provider round-trip
# ---------------------------------------------------------------------------


async def test_critic_returns_low_score_for_on_track_response() -> None:
    _fake, factory = factory_for(
        [
            text_message(
                '{"drift_score": 0.1, "on_track": true, "reason": "Steps match the plan."}'
            ),
            result_message(),
        ]
    )
    provider = AnthropicProvider(client_factory=factory)
    report = await run_critic_checkpoint(
        provider,
        user_message="Investigate prior art.",
        plan={"goal": "x", "nodes": [{"description": "do thing", "rationale": "reason"}]},
        action_log=[],
        threshold_label="25%",
    )
    assert isinstance(report, CriticReport)
    assert report.drift_score == 0.1
    assert report.on_track is True
    assert "Steps match" in report.reason


async def test_critic_falls_back_to_on_track_when_response_is_not_json() -> None:
    _fake, factory = factory_for(
        [
            text_message("This is some prose, not JSON."),
            result_message(),
        ]
    )
    provider = AnthropicProvider(client_factory=factory)
    report = await run_critic_checkpoint(
        provider,
        user_message="Hi",
        plan=None,
        action_log=[],
        threshold_label="25%",
    )
    # Permissive fallback so a flaky parse doesn't kill the run.
    assert report.drift_score == 0.0
    assert report.on_track is True


# ---------------------------------------------------------------------------
# Critic-node integration — the graph wires it for real
# ---------------------------------------------------------------------------


async def test_critic_runs_at_threshold_and_records_drift_in_index(
    tmp_path: Path,
) -> None:
    """A budget that crosses 25 % drives one critic LLM call; the
    drift score lands on the run header."""
    _fake, factory = factory_for(
        [
            # Plan turn.
            text_message('{"goal": "x", "steps": [{"description":"step","rationale":"r"}]}'),
            result_message(),
            # Critic turn — fires from critic_node when iteration count
            # crosses 25 % (3rd iteration with maxIterations=12 = 25 %).
            text_message('{"drift_score": 0.2, "on_track": true, "reason": "Looks fine."}'),
            result_message(),
            # Respond turn.
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
        session_id="s",
        provider_id="anthropic",
        prompt="Hi",
        notify=notify,
        budget=Budget(max_iterations=12),
    )
    finished = await runner.approve_plan(
        run_id=paused.run_id,
        provider_id="anthropic",
        decision="approve",
        notify=notify,
    )
    assert finished is not None
    assert finished.status == RunStatus.COMPLETED.value

    drift_entries = [
        params
        for method, params in captured
        if method == "run.action_log"
        and params["entry"]["kind"] == "drift_check"
        and params["entry"]["payload"].get("step") == "critic"
    ]
    assert drift_entries, "expected at least one critic drift_check entry"

    header = await store.get(paused.run_id)
    assert header is not None
    assert header.drift_score == 0.2


async def test_high_drift_score_pauses_run_with_drift_gate(tmp_path: Path) -> None:
    """A critic verdict above the pause threshold halts the run with
    a `gateKind: "drift"` notification."""
    _fake, factory = factory_for(
        [
            # Plan.
            text_message('{"goal": "x", "steps": [{"description":"step","rationale":"r"}]}'),
            result_message(),
            # Critic — high drift score.
            text_message('{"drift_score": 0.9, "on_track": false, "reason": "Wandered off-goal."}'),
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
        prompt="Hi",
        notify=notify,
        budget=Budget(max_iterations=12),
    )
    finished = await runner.approve_plan(
        run_id=paused.run_id,
        provider_id="anthropic",
        decision="approve",
        notify=notify,
    )
    assert finished is not None
    assert finished.status == RunStatus.KILLED.value

    drift_gates = [
        params
        for method, params in captured
        if method == "run.approval_required" and params.get("gateKind") == "drift"
    ]
    assert len(drift_gates) >= 1
    assert drift_gates[0]["driftScore"] == 0.9
    assert "off-goal" in drift_gates[0]["reason"]

    header = await store.get(paused.run_id)
    assert header is not None
    assert header.status == RunStatus.KILLED.value
    assert header.drift_score == 0.9


async def test_no_budget_no_critic_invocation(tmp_path: Path) -> None:
    """Without a budget the critic doesn't fire — the run runs as
    before."""
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

    captured, notify = _captured()
    paused = await runner.run(
        session_id="s",
        provider_id="anthropic",
        prompt="Hi",
        notify=notify,
    )
    await runner.approve_plan(
        run_id=paused.run_id,
        provider_id="anthropic",
        decision="approve",
        notify=notify,
    )

    critic_entries = [
        params
        for method, params in captured
        if method == "run.action_log"
        and params["entry"]["kind"] == "drift_check"
        and params["entry"]["payload"].get("step") == "critic"
    ]
    assert critic_entries == []
