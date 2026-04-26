"""JSON-RPC bindings for the LSP scaffolding."""

from __future__ import annotations

from typing import Any

from thalyn_brain.lsp import LspError, LspManager, LspNotAvailableError
from thalyn_brain.rpc import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    Dispatcher,
    JsonValue,
    Notifier,
    RpcError,
    RpcParams,
)


def register_lsp_methods(dispatcher: Dispatcher, manager: LspManager) -> None:
    async def lsp_start(params: RpcParams, notify: Notifier) -> JsonValue:
        # Bind the manager's notification sink to this connection's
        # writer so server-initiated messages flow back to the
        # renderer for the lifetime of the brain process.
        manager.configure_notify(notify)
        language = _require_str(params, "language")
        try:
            session = await manager.start(language)
        except LspNotAvailableError as exc:
            raise RpcError(
                code=INTERNAL_ERROR,
                message=str(exc),
                data={"reason": "not-available"},
            ) from exc
        except LspError as exc:
            raise RpcError(code=INTERNAL_ERROR, message=str(exc)) from exc
        return {
            "sessionId": session.session_id,
            "language": session.language,
            "command": session.command,
            "startedAtMs": session.started_at_ms,
        }

    async def lsp_send(params: RpcParams) -> JsonValue:
        session_id = _require_str(params, "sessionId")
        message = params.get("message")
        if not isinstance(message, dict):
            raise RpcError(code=INVALID_PARAMS, message="message must be an object")
        try:
            await manager.send(session_id, message)
        except LspError as exc:
            raise RpcError(code=INTERNAL_ERROR, message=str(exc)) from exc
        return {"queued": True}

    async def lsp_stop(params: RpcParams) -> JsonValue:
        session_id = _require_str(params, "sessionId")
        stopped = await manager.stop(session_id)
        return {"stopped": stopped, "sessionId": session_id}

    async def lsp_list(_: RpcParams) -> JsonValue:
        sessions: Any = manager.list_sessions()
        return {"sessions": sessions}

    dispatcher.register_streaming("lsp.start", lsp_start)
    dispatcher.register("lsp.send", lsp_send)
    dispatcher.register("lsp.stop", lsp_stop)
    dispatcher.register("lsp.list", lsp_list)


def _require_str(params: RpcParams, key: str) -> str:
    value = params.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RpcError(code=INVALID_PARAMS, message=f"missing or non-string {key!r}")
    return value
