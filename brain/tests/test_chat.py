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


async def test_chat_send_streams_chunks_via_notifications() -> None:
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
    assert response["result"]["sessionId"] == "sess_1"
    assert response["result"]["chunks"] == 4  # start + 2 text + stop
    assert response["result"]["reason"] == "end_turn"
    assert response["result"]["totalCostUsd"] == 0.0002

    methods = [method for method, _ in captured]
    assert methods == ["chat.chunk"] * 4

    chunk_kinds = [params["chunk"]["kind"] for _, params in captured]
    assert chunk_kinds == ["start", "text", "text", "stop"]
    assert all(params["sessionId"] == "sess_1" for _, params in captured)


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
                "sessionId": "sess_2",
                "providerId": "anthropic",
                "prompt": "ls files",
            },
        },
        notify,
    )

    chunk_kinds = [params["chunk"]["kind"] for _, params in captured]
    assert "tool_call" in chunk_kinds


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
