"""Tests for the chat handler."""

from __future__ import annotations

from typing import Any

import pytest
from thalyn_brain.chat import register_chat_methods
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


async def test_chat_send_routes_through_graph_and_streams_chunks() -> None:
    _fake, factory = factory_for(
        [
            text_message("Hello, "),
            text_message("world."),
            result_message(total_cost_usd=0.0002),
        ]
    )
    provider = AnthropicProvider(client_factory=factory)
    registry = _registry_with(provider)

    dispatcher = Dispatcher()
    register_chat_methods(dispatcher, registry)

    captured, notify = _captured_notifier()
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
    assert result["status"] == "completed"
    assert result["runId"].startswith("r_")
    assert result["plan"]["goal"] == "Hi"
    assert len(result["plan"]["nodes"]) == 1

    chat_chunk_kinds = _kinds_for_method(captured, "chat.chunk")
    assert chat_chunk_kinds == ["start", "text", "text", "stop"]
    chat_chunks = [params for method, params in captured if method == "chat.chunk"]
    assert all(p["sessionId"] == "sess_1" for p in chat_chunks)


async def test_chat_send_emits_run_lifecycle_notifications() -> None:
    _fake, factory = factory_for([text_message("ok."), result_message()])
    provider = AnthropicProvider(client_factory=factory)
    registry = _registry_with(provider)

    dispatcher = Dispatcher()
    register_chat_methods(dispatcher, registry)

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

    methods = [method for method, _ in captured]
    statuses = [params["status"] for method, params in captured if method == "run.status"]
    assert statuses[0] == "pending"
    assert "planning" in statuses
    assert "running" in statuses
    assert statuses[-1] == "completed"

    plan_updates = [params for method, params in captured if method == "run.plan_update"]
    assert len(plan_updates) == 1
    assert plan_updates[0]["plan"]["goal"] == "Hello"

    action_log_entries = [params for method, params in captured if method == "run.action_log"]
    assert len(action_log_entries) >= 2  # at least the plan + node transitions

    assert "chat.chunk" in methods


async def test_chat_send_includes_tool_call_chunks() -> None:
    _fake, factory = factory_for(
        [
            tool_call_message(call_id="t_1", name="Bash", input_={"command": "ls"}),
            text_message("Done."),
            result_message(),
        ]
    )
    provider = AnthropicProvider(client_factory=factory)
    registry = _registry_with(provider)

    dispatcher = Dispatcher()
    register_chat_methods(dispatcher, registry)

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
    assert "tool_call" in chat_chunk_kinds

    # The tool call also lands in the action log.
    action_log_payloads = [
        params["entry"]["payload"] for method, params in captured if method == "run.action_log"
    ]
    tool_call_actions = [p for p in action_log_payloads if p.get("tool") == "Bash"]
    assert len(tool_call_actions) >= 1


@pytest.mark.parametrize(
    "params",
    [
        {"providerId": "anthropic", "prompt": "hi"},  # missing sessionId
        {"sessionId": "s", "prompt": "hi"},  # missing providerId
        {"sessionId": "s", "providerId": "anthropic"},  # missing prompt
    ],
)
async def test_missing_required_params_returns_invalid_params(
    params: dict[str, Any],
) -> None:
    registry = ProviderRegistry()
    dispatcher = Dispatcher()
    register_chat_methods(dispatcher, registry)
    _, notify = _captured_notifier()

    response = await dispatcher.handle(
        {"jsonrpc": "2.0", "id": 1, "method": "chat.send", "params": params},
        notify,
    )

    assert response is not None
    assert response["error"]["code"] == -32602  # INVALID_PARAMS


async def test_unknown_provider_returns_invalid_params() -> None:
    registry = ProviderRegistry()
    dispatcher = Dispatcher()
    register_chat_methods(dispatcher, registry)
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
