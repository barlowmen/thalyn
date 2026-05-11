"""Tests for the action registry primitive.

The registry is the conversational substrate for every configurable
surface in Thalyn (per F9.4 / F9.5). Coverage here is structural:

- ``register`` indexes actions and rejects duplicates.
- ``list_summaries`` returns the lean shape (name + description),
  not the full input schema.
- ``describe`` returns the schema on demand.
- ``execute`` validates inputs against the schema, drops unknown
  keys, and surfaces a clear error for missing required slots.
- Hard-gated actions refuse to execute without the approval flag.
- The matcher pipeline returns the first hit and ``None`` when
  nothing matches; ``ActionMatch.with_inputs`` folds in new fields
  and prunes resolved ``missing_inputs``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest
from thalyn_brain.action_registry import (
    Action,
    ActionInput,
    ActionMatch,
    ActionRegistry,
    ActionRegistryError,
    ActionResult,
    ActionValidationError,
    HardGateNotResolvedError,
    UnknownActionError,
    collect_missing_required,
)


async def _noop_executor(_: Mapping[str, Any]) -> ActionResult:
    return ActionResult(confirmation="done")


def _build_action(
    name: str = "test.echo",
    *,
    hard_gate: bool = False,
    inputs: tuple[ActionInput, ...] = (),
) -> Action:
    return Action(
        name=name,
        description=f"echo action for {name}",
        inputs=inputs,
        executor=_noop_executor,
        hard_gate=hard_gate,
        hard_gate_kind="external_send" if hard_gate else None,
    )


def test_register_indexes_actions_and_rejects_duplicates() -> None:
    registry = ActionRegistry()
    registry.register(_build_action("test.alpha"))
    with pytest.raises(ActionRegistryError):
        registry.register(_build_action("test.alpha"))


def test_list_summaries_returns_lean_shape() -> None:
    registry = ActionRegistry()
    registry.register(_build_action("test.beta"))
    registry.register(_build_action("test.alpha"))
    summaries = registry.list_summaries()
    assert [s.name for s in summaries] == ["test.alpha", "test.beta"]
    assert all(s.description for s in summaries)
    assert all(hasattr(s, "hard_gate") for s in summaries)


def test_describe_returns_full_schema_on_demand() -> None:
    registry = ActionRegistry()
    registry.register(
        _build_action(
            "test.with_inputs",
            inputs=(
                ActionInput(name="who", description="recipient", kind="string"),
                ActionInput(
                    name="mode",
                    description="mode",
                    choices=("send", "draft"),
                ),
                ActionInput(
                    name="dry_run",
                    description="dry run",
                    kind="bool",
                    required=False,
                ),
            ),
        )
    )
    schema = registry.describe("test.with_inputs")
    assert schema["name"] == "test.with_inputs"
    assert schema["hardGate"] is False
    assert [slot["name"] for slot in schema["inputs"]] == [
        "who",
        "mode",
        "dry_run",
    ]
    assert schema["inputs"][1]["choices"] == ["send", "draft"]
    assert schema["inputs"][2]["required"] is False


def test_describe_unknown_action_raises() -> None:
    registry = ActionRegistry()
    with pytest.raises(UnknownActionError):
        registry.describe("nope")


async def test_execute_validates_and_invokes_executor() -> None:
    seen: dict[str, Any] = {}

    async def executor(inputs: Mapping[str, Any]) -> ActionResult:
        seen.update(inputs)
        return ActionResult(confirmation="ok")

    registry = ActionRegistry()
    registry.register(
        Action(
            name="test.execute",
            description="…",
            inputs=(
                ActionInput(name="who", description="who"),
                ActionInput(name="extra", description="extra", required=False),
            ),
            executor=executor,
        )
    )
    result = await registry.execute(
        "test.execute",
        {"who": "barlow", "extra": "ignored-required-false", "bogus": "dropped"},
    )
    assert result.confirmation == "ok"
    # ``bogus`` is dropped — only declared inputs reach the executor.
    assert seen == {"who": "barlow", "extra": "ignored-required-false"}


async def test_execute_rejects_missing_required_inputs() -> None:
    registry = ActionRegistry()
    registry.register(
        _build_action(
            inputs=(ActionInput(name="who", description="recipient"),),
        )
    )
    with pytest.raises(ActionValidationError):
        await registry.execute("test.echo", {})


async def test_execute_rejects_invalid_choice() -> None:
    registry = ActionRegistry()
    registry.register(
        _build_action(
            inputs=(
                ActionInput(
                    name="mode",
                    description="mode",
                    choices=("send", "draft"),
                ),
            ),
        )
    )
    with pytest.raises(ActionValidationError):
        await registry.execute("test.echo", {"mode": "blast"})


async def test_execute_refuses_hard_gate_without_resolution() -> None:
    registry = ActionRegistry()
    registry.register(_build_action("test.publish", hard_gate=True))
    with pytest.raises(HardGateNotResolvedError):
        await registry.execute("test.publish", {})


async def test_execute_runs_hard_gate_when_resolved() -> None:
    registry = ActionRegistry()
    registry.register(_build_action("test.publish", hard_gate=True))
    result = await registry.execute("test.publish", {}, hard_gate_resolved=True)
    assert result.confirmation == "done"


def test_collect_missing_required_helper() -> None:
    action = _build_action(
        inputs=(
            ActionInput(name="who", description="who"),
            ActionInput(name="opt", description="opt", required=False),
            ActionInput(name="when", description="when"),
        )
    )
    assert collect_missing_required(action, {"who": "barlow"}) == ("when",)
    assert collect_missing_required(action, {}) == ("who", "when")
    assert collect_missing_required(action, {"who": "barlow", "when": "now"}) == ()


def test_match_pipeline_returns_first_hit() -> None:
    registry = ActionRegistry()
    registry.register(_build_action("test.alpha"))
    registry.register(_build_action("test.beta"))

    class AlphaMatcher:
        def try_match(
            self,
            prompt: str,
            *,
            context: Mapping[str, Any],
        ) -> ActionMatch | None:
            if "alpha" in prompt.lower():
                return ActionMatch(action_name="test.alpha")
            return None

    class BetaMatcher:
        def try_match(
            self,
            prompt: str,
            *,
            context: Mapping[str, Any],
        ) -> ActionMatch | None:
            if "beta" in prompt.lower():
                return ActionMatch(action_name="test.beta")
            return None

    registry.register_matcher(AlphaMatcher())
    registry.register_matcher(BetaMatcher())
    alpha = registry.try_match("please do alpha")
    beta = registry.try_match("please do beta")
    assert alpha is not None and alpha.action_name == "test.alpha"
    assert beta is not None and beta.action_name == "test.beta"
    assert registry.try_match("nothing here") is None


def test_action_match_with_inputs_folds_and_prunes_missing() -> None:
    match = ActionMatch(
        action_name="test.act",
        inputs={"a": 1},
        missing_inputs=("b", "c"),
    )
    folded = match.with_inputs({"b": 2})
    assert folded.inputs == {"a": 1, "b": 2}
    assert folded.missing_inputs == ("c",)
