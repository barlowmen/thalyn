"""JSON-RPC 2.0 dispatch for the brain sidecar.

Methods are registered explicitly through ``Dispatcher.register`` so the
wire surface remains auditable. Handlers may also emit JSON-RPC
notifications while a request is in flight by accepting a
``Notifier`` argument — useful for streaming chat tokens, tool calls,
and run lifecycle events without inventing a parallel transport.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from thalyn_brain import __version__

JsonValue = Any
RpcParams = dict[str, JsonValue]


class Notifier(Protocol):
    """Callable handlers receive to emit notifications mid-request."""

    async def __call__(self, method: str, params: JsonValue) -> None: ...


PlainHandler = Callable[[RpcParams], Awaitable[JsonValue]]
StreamingHandler = Callable[[RpcParams, Notifier], Awaitable[JsonValue]]
Handler = PlainHandler | StreamingHandler

# JSON-RPC 2.0 reserved error codes.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

# Application-defined error code (JSON-RPC reserves -32099 to -32000
# for server-defined errors). Used for v2 IPC methods that are
# registered as scaffolding but not yet implemented; the caller can
# distinguish "not implemented" from "method not found" or
# "internal error" without parsing the message string.
NOT_IMPLEMENTED = -32001


@dataclass(frozen=True)
class RpcError(Exception):
    """A structured JSON-RPC error to surface to the caller."""

    code: int
    message: str
    data: JsonValue = None


class Dispatcher:
    """Map method names to async handlers and run requests through them."""

    def __init__(self) -> None:
        self._handlers: dict[str, tuple[Handler, bool]] = {}

    def register(self, method: str, handler: PlainHandler) -> None:
        """Register a non-streaming method handler."""
        self._register(method, handler, streaming=False)

    def register_streaming(self, method: str, handler: StreamingHandler) -> None:
        """Register a handler that may emit notifications mid-request."""
        self._register(method, handler, streaming=True)

    def _register(self, method: str, handler: Handler, *, streaming: bool) -> None:
        if method in self._handlers:
            raise ValueError(f"method already registered: {method}")
        self._handlers[method] = (handler, streaming)

    async def handle(
        self,
        request: JsonValue,
        notify: Notifier,
    ) -> dict[str, JsonValue] | None:
        """Dispatch a single decoded JSON-RPC request.

        ``notify`` is the side-channel handlers use to emit
        notifications without blocking on the response. Returns the
        response object, or ``None`` for notifications (requests
        without an ``id`` field, per the JSON-RPC 2.0 spec).
        """
        if not isinstance(request, dict):
            return _error_response(None, INVALID_REQUEST, "request must be a JSON object")

        rpc_id = request.get("id")
        method = request.get("method")
        params = request.get("params", {})

        if not isinstance(method, str) or not method:
            return _error_response(rpc_id, INVALID_REQUEST, "missing or non-string method")
        if not isinstance(params, dict):
            return _error_response(rpc_id, INVALID_PARAMS, "params must be an object")

        entry = self._handlers.get(method)
        if entry is None:
            return _error_response(rpc_id, METHOD_NOT_FOUND, f"method not found: {method}")

        handler, streaming = entry
        try:
            if streaming:
                streaming_handler: StreamingHandler = handler  # type: ignore[assignment]
                result = await streaming_handler(params, notify)
            else:
                plain_handler: PlainHandler = handler  # type: ignore[assignment]
                result = await plain_handler(params)
        except RpcError as exc:
            return _error_response(rpc_id, exc.code, exc.message, exc.data)
        except Exception as exc:
            return _error_response(rpc_id, INTERNAL_ERROR, str(exc))

        # Notification (no id) — JSON-RPC 2.0 says no response is sent.
        if rpc_id is None and "id" not in request:
            return None

        return {"jsonrpc": "2.0", "id": rpc_id, "result": result}


def _error_response(
    rpc_id: JsonValue,
    code: int,
    message: str,
    data: JsonValue = None,
) -> dict[str, JsonValue]:
    error: dict[str, JsonValue] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": rpc_id, "error": error}


async def _ping(_params: RpcParams) -> JsonValue:
    return {
        "pong": True,
        "version": __version__,
        "epoch_ms": int(time.time() * 1000),
    }


def build_default_dispatcher() -> Dispatcher:
    """Build a dispatcher with the always-on methods.

    Chat methods are registered separately by the entry point so the
    provider registry can be wired in cleanly.
    """
    dispatcher = Dispatcher()
    dispatcher.register("ping", _ping)
    return dispatcher
