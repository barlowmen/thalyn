"""Budget data + enforcement tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from thalyn_brain.orchestration import Runner
from thalyn_brain.orchestration.budget import (
    Budget,
    BudgetCheck,
    BudgetConsumption,
    check_budget,
    estimate_tokens_from_text,
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
# Pure-function tests
# ---------------------------------------------------------------------------


def test_unbounded_budget_never_exceeds() -> None:
    budget = Budget()
    consumed = BudgetConsumption(tokens_used=10_000, iterations=999)
    assert not check_budget(budget, consumed).exceeded


def test_iteration_cap_trips_first_when_over() -> None:
    budget = Budget(max_iterations=2, max_tokens=100, max_seconds=10)
    consumed = BudgetConsumption(iterations=5)
    decision = check_budget(budget, consumed)
    assert decision.exceeded
    assert decision.dimension == "iterations"
    assert decision.actual == 5.0
    assert decision.limit == 2.0


def test_token_cap_trips_when_over_other_dimensions_clear() -> None:
    budget = Budget(max_tokens=100)
    consumed = BudgetConsumption(tokens_used=200, iterations=1)
    decision = check_budget(budget, consumed)
    assert decision.exceeded
    assert decision.dimension == "tokens"
    assert decision.limit == 100.0


def test_token_estimator_is_char_div_4_floor() -> None:
    assert estimate_tokens_from_text("") == 0
    assert estimate_tokens_from_text("a") == 1
    assert estimate_tokens_from_text("a" * 12) == 3


def test_budget_round_trips_through_wire() -> None:
    original = Budget(max_tokens=5000, max_seconds=30, max_iterations=20)
    rebuilt = Budget.from_wire(original.to_wire())
    assert rebuilt == original


def test_consumption_with_iteration_increments_only_iterations() -> None:
    base = BudgetConsumption(tokens_used=10, iterations=2)
    bumped = base.with_iteration()
    assert bumped.tokens_used == 10
    assert bumped.iterations == 3


def test_consumption_with_tokens_clamps_negative_deltas() -> None:
    base = BudgetConsumption(tokens_used=10)
    assert base.with_tokens(-5).tokens_used == 10
    assert base.with_tokens(7).tokens_used == 17


def test_decision_reason_is_human_readable() -> None:
    decision = BudgetCheck(exceeded=True, dimension="tokens", limit=100.0, actual=200.0)
    assert "tokens budget exceeded" in decision.reason


# ---------------------------------------------------------------------------
# Runner integration — iteration cap trips inside the graph
# ---------------------------------------------------------------------------


async def test_iteration_cap_kills_run_at_first_node(tmp_path: Path) -> None:
    """An impossible iteration cap (1) trips on the planner node and
    halts the run before respond ever streams."""
    _fake, factory = factory_for(
        [
            text_message('{"goal": "x", "steps": []}'),
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
        budget=Budget(max_iterations=0),
    )
    assert paused.status == RunStatus.KILLED.value

    # A budget gate notification fired with the dimension that broke.
    gates = [
        params
        for method, params in captured
        if method == "run.approval_required" and params.get("gateKind") == "budget"
    ]
    assert len(gates) >= 1
    assert gates[0]["dimension"] == "iterations"

    # The runs index reflects the killed status and a non-zero
    # iteration count.
    header = await store.get(paused.run_id)
    assert header is not None
    assert header.status == RunStatus.KILLED.value
    assert header.budget is not None
    assert header.budget["maxIterations"] == 0
    assert header.budget_consumed is not None
    assert header.budget_consumed["iterations"] >= 1


async def test_token_cap_kills_after_planner_inflates_consumption(
    tmp_path: Path,
) -> None:
    """A small token cap is exceeded after the planner's text is
    folded into the consumption tally — the run halts before respond."""
    inflated_steps = ('{"description":"step","rationale":"r"},' * 20).rstrip(",")
    plan_text = '{"goal": "x", "steps": [' + inflated_steps + "]}"
    _fake, factory = factory_for(
        [
            text_message(plan_text),
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
        budget=Budget(max_tokens=10),
    )

    # The budget breach lands as KILLED with a token-dimension gate.
    assert paused.status == RunStatus.KILLED.value
    token_gates = [
        params
        for method, params in captured
        if method == "run.approval_required"
        and params.get("gateKind") == "budget"
        and params.get("dimension") == "tokens"
    ]
    assert len(token_gates) >= 1

    header = await store.get(paused.run_id)
    assert header is not None
    assert header.status == RunStatus.KILLED.value


async def test_unbounded_budget_lets_run_complete(tmp_path: Path) -> None:
    """No budget set → no enforcement, run completes normally."""
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
        prompt="Hi",
        notify=notify,
    )
    finished = await runner.approve_plan(
        run_id=paused.run_id,
        provider_id="anthropic",
        decision="approve",
        notify=notify,
    )
    assert finished is not None
    assert finished.status == RunStatus.COMPLETED.value

    header = await store.get(paused.run_id)
    assert header is not None
    # Budget defaults to None / unbounded.
    assert header.budget is None
    # Consumption is still tracked even with no cap.
    assert header.budget_consumed is not None
    assert header.budget_consumed["iterations"] >= 1
