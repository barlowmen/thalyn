"""Tests for the JSON-RPC dispatcher."""

from __future__ import annotations

from typing import Any

import pytest
from thalyn_brain.rpc import (
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    Dispatcher,
    Notifier,
    RpcError,
    build_default_dispatcher,
)


@pytest.fixture
def dispatcher() -> Dispatcher:
    return build_default_dispatcher()


def make_notifier() -> tuple[list[tuple[str, Any]], Notifier]:
    """Returns the recorded notifications list and a Notifier that
    appends to it."""
    captured: list[tuple[str, Any]] = []

    async def notify(method: str, params: Any) -> None:
        captured.append((method, params))

    return captured, notify


async def test_ping_returns_pong(dispatcher: Dispatcher) -> None:
    _, notify = make_notifier()
    response = await dispatcher.handle({"jsonrpc": "2.0", "id": 1, "method": "ping"}, notify)
    assert response is not None
    assert response["id"] == 1
    assert response["jsonrpc"] == "2.0"
    result = response["result"]
    assert result["pong"] is True
    assert isinstance(result["epoch_ms"], int)
    assert isinstance(result["version"], str)


async def test_unknown_method_returns_method_not_found(dispatcher: Dispatcher) -> None:
    _, notify = make_notifier()
    response = await dispatcher.handle({"jsonrpc": "2.0", "id": 7, "method": "nope"}, notify)
    assert response is not None
    assert response["error"]["code"] == METHOD_NOT_FOUND


async def test_non_object_request_is_invalid(dispatcher: Dispatcher) -> None:
    _, notify = make_notifier()
    response = await dispatcher.handle("not an object", notify)
    assert response is not None
    assert response["error"]["code"] == INVALID_REQUEST


async def test_non_string_method_is_invalid(dispatcher: Dispatcher) -> None:
    _, notify = make_notifier()
    response = await dispatcher.handle({"jsonrpc": "2.0", "id": 1, "method": 42}, notify)
    assert response is not None
    assert response["error"]["code"] == INVALID_REQUEST


async def test_non_object_params_is_invalid(dispatcher: Dispatcher) -> None:
    _, notify = make_notifier()
    response = await dispatcher.handle(
        {"jsonrpc": "2.0", "id": 1, "method": "ping", "params": [1, 2]},
        notify,
    )
    assert response is not None
    assert response["error"]["code"] == INVALID_PARAMS


async def test_notification_returns_no_response(dispatcher: Dispatcher) -> None:
    _, notify = make_notifier()
    response = await dispatcher.handle({"jsonrpc": "2.0", "method": "ping"}, notify)
    assert response is None


async def test_handler_rpc_error_propagates() -> None:
    dispatcher = Dispatcher()

    async def boom(_params: dict[str, object]) -> object:
        raise RpcError(code=-32099, message="custom failure", data={"why": "test"})

    dispatcher.register("boom", boom)
    _, notify = make_notifier()
    response = await dispatcher.handle({"jsonrpc": "2.0", "id": 9, "method": "boom"}, notify)
    assert response is not None
    assert response["error"]["code"] == -32099
    assert response["error"]["message"] == "custom failure"
    assert response["error"]["data"] == {"why": "test"}


async def test_register_duplicate_raises() -> None:
    dispatcher = Dispatcher()

    async def noop(_params: dict[str, object]) -> object:
        return None

    dispatcher.register("x", noop)
    with pytest.raises(ValueError):
        dispatcher.register("x", noop)


async def test_streaming_handler_can_emit_notifications() -> None:
    dispatcher = Dispatcher()

    async def streamer(params: dict[str, object], notify: Notifier) -> object:
        await notify("progress", {"step": 1})
        await notify("progress", {"step": 2})
        return {"echoed": params.get("value")}

    dispatcher.register_streaming("stream", streamer)

    captured, notify = make_notifier()
    response = await dispatcher.handle(
        {"jsonrpc": "2.0", "id": 11, "method": "stream", "params": {"value": "hi"}},
        notify,
    )

    assert response is not None
    assert response["result"] == {"echoed": "hi"}
    assert captured == [("progress", {"step": 1}), ("progress", {"step": 2})]
