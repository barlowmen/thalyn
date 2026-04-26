"""Tests for the chat handler."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from thalyn_brain.chat import register_chat_methods
from thalyn_brain.orchestration import Runner
from thalyn_brain.provider import AnthropicProvider, ProviderRegistry
from thalyn_brain.rpc import Dispatcher, Notifier

from tests.provider._fake_sdk import (
    factory_for,
    result_message,
    text_message,
    tool_call_message,
)


def _captured_notifier() -> tuple[list[tuple[str, Any]], Notifier]:
    captured: list[tuple[str, Any]] = []

    async def notify(method: str, params: Any) -> None:
        captured.append((method, params))

    return captured, notify


def _registry_with(provider: AnthropicProvider) -> ProviderRegistry:
    """Override the Anthropic slot in the registry with a stub-driven instance."""
    registry = ProviderRegistry()
    registry._providers["anthropic"] = provider
    return registry


def _kinds_for_method(captured: list[tuple[str, Any]], method: str) -> list[str]:
    return [
        params["chunk"]["kind"] for emitted_method, params in captured if emitted_method == method
    ]


def _wire(dispatcher: Dispatcher, registry: ProviderRegistry, tmp_path: Path) -> None:
    """Build a runner with persistence so the interrupt fires."""
    runner = Runner(registry, data_dir=tmp_path)
    register_chat_methods(dispatcher, registry, runner=runner)


async def test_chat_send_pauses_at_plan_approval_interrupt(tmp_path: Path) -> None:
    """chat.send returns awaiting_approval after the planner runs.

    The actual chat-chunk stream lands during run.approve_plan; here we
    just verify that the planner phase fires and the run is paused.
    """
    _fake, factory = factory_for(
        [
            text_message('{"goal": "Hi", "steps": []}'),
            result_message(),
        ]
    )
    provider = AnthropicProvider(client_factory=factory)
    registry = _registry_with(provider)

    dispatcher = Dispatcher()
    _wire(dispatcher, registry, tmp_path)

    _captured, notify = _captured_notifier()
    response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "chat.send",
            "params": {
                "sessionId": "sess_1",
                "providerId": "anthropic",
                "prompt": "Hi",
            },
        },
        notify,
    )

    assert response is not None
    result = response["result"]
    assert result["sessionId"] == "sess_1"
    assert result["status"] == "awaiting_approval"
    assert result["runId"].startswith("r_")
    assert result["plan"]["goal"] == "Hi"


async def test_chat_send_emits_planning_lifecycle_then_pauses(tmp_path: Path) -> None:
    _fake, factory = factory_for([text_message('{"goal": "Hello", "steps": []}'), result_message()])
    provider = AnthropicProvider(client_factory=factory)
    registry = _registry_with(provider)

    dispatcher = Dispatcher()
    _wire(dispatcher, registry, tmp_path)

    captured, notify = _captured_notifier()
    await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "chat.send",
            "params": {
                "sessionId": "sess_2",
                "providerId": "anthropic",
                "prompt": "Hello",
            },
        },
        notify,
    )

    statuses = [params["status"] for method, params in captured if method == "run.status"]
    assert statuses[0] == "pending"
    assert "planning" in statuses
    assert statuses[-1] == "awaiting_approval"

    plan_updates = [params for method, params in captured if method == "run.plan_update"]
    assert len(plan_updates) == 1
    assert plan_updates[0]["plan"]["goal"] == "Hello"

    approval_required = [params for method, params in captured if method == "run.approval_required"]
    assert len(approval_required) == 1


async def test_chat_send_chat_chunks_only_arrive_after_approval(tmp_path: Path) -> None:
    """Tool-call chunks live in the response phase; verify chat.send
    on its own does not produce respond-phase chunks."""
    _fake, factory = factory_for(
        [
            tool_call_message(call_id="t_1", name="Bash", input_={"command": "ls"}),
            text_message('{"goal": "ls", "steps": []}'),
            result_message(),
        ]
    )
    provider = AnthropicProvider(client_factory=factory)
    registry = _registry_with(provider)

    dispatcher = Dispatcher()
    _wire(dispatcher, registry, tmp_path)

    captured, notify = _captured_notifier()
    await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "chat.send",
            "params": {
                "sessionId": "sess_3",
                "providerId": "anthropic",
                "prompt": "ls files",
            },
        },
        notify,
    )

    chat_chunk_kinds = _kinds_for_method(captured, "chat.chunk")
    # No chat.chunk events: respond hasn't run yet.
    assert chat_chunk_kinds == []


@pytest.mark.parametrize(
    "params",
    [
        {"providerId": "anthropic", "prompt": "hi"},  # missing sessionId
        {"sessionId": "s", "prompt": "hi"},  # missing providerId
        {"sessionId": "s", "providerId": "anthropic"},  # missing prompt
    ],
)
async def test_missing_required_params_returns_invalid_params(
    tmp_path: Path,
    params: dict[str, Any],
) -> None:
    registry = ProviderRegistry()
    dispatcher = Dispatcher()
    _wire(dispatcher, registry, tmp_path)
    _, notify = _captured_notifier()

    response = await dispatcher.handle(
        {"jsonrpc": "2.0", "id": 1, "method": "chat.send", "params": params},
        notify,
    )

    assert response is not None
    assert response["error"]["code"] == -32602  # INVALID_PARAMS


async def test_unknown_provider_returns_invalid_params(tmp_path: Path) -> None:
    registry = ProviderRegistry()
    dispatcher = Dispatcher()
    _wire(dispatcher, registry, tmp_path)
    _, notify = _captured_notifier()

    response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "chat.send",
            "params": {
                "sessionId": "s",
                "providerId": "no-such-provider",
                "prompt": "hi",
            },
        },
        notify,
    )

    assert response is not None
    assert response["error"]["code"] == -32602
