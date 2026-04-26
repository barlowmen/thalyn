"""JSON-RPC 2.0 dispatch for the brain sidecar.

The dispatch surface is intentionally tiny at this point: a single ``ping``
method that returns a structured pong. Methods are registered explicitly
through ``Dispatcher.register`` so the wire surface remains auditable.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from thalyn_brain import __version__

JsonValue = Any
RpcParams = dict[str, JsonValue]
Handler = Callable[[RpcParams], Awaitable[JsonValue]]

# JSON-RPC 2.0 reserved error codes.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


@dataclass(frozen=True)
class RpcError(Exception):
    """A structured JSON-RPC error to surface to the caller."""

    code: int
    message: str
    data: JsonValue = None


class Dispatcher:
    """Map method names to async handlers and run requests through them."""

    def __init__(self) -> None:
        self._handlers: dict[str, Handler] = {}

    def register(self, method: str, handler: Handler) -> None:
        if method in self._handlers:
            raise ValueError(f"method already registered: {method}")
        self._handlers[method] = handler

    async def handle(self, request: JsonValue) -> dict[str, JsonValue] | None:
        """Dispatch a single decoded JSON-RPC request.

        Returns the response object, or ``None`` for notifications (requests
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

        handler = self._handlers.get(method)
        if handler is None:
            return _error_response(rpc_id, METHOD_NOT_FOUND, f"method not found: {method}")

        try:
            result = await handler(params)
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
    dispatcher = Dispatcher()
    dispatcher.register("ping", _ping)
    return dispatcher
