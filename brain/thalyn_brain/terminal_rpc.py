"""JSON-RPC bindings for terminal observation + agent attach."""

from __future__ import annotations

from thalyn_brain.rpc import (
    INVALID_PARAMS,
    Dispatcher,
    JsonValue,
    RpcError,
    RpcParams,
)
from thalyn_brain.terminal_observer import (
    PER_SESSION_BUFFER_CHARS,
    TerminalObserver,
)


def register_terminal_methods(dispatcher: Dispatcher, observer: TerminalObserver) -> None:
    async def terminal_observe(params: RpcParams) -> JsonValue:
        session_id = _require_str(params, "sessionId")
        seq_value = params.get("seq", 0)
        seq = seq_value if isinstance(seq_value, int) else 0
        data = params.get("data")
        if not isinstance(data, str):
            raise RpcError(code=INVALID_PARAMS, message="data must be a string")
        await observer.observe(session_id, seq, data)
        return {"observed": True}

    async def terminal_forget(params: RpcParams) -> JsonValue:
        session_id = _require_str(params, "sessionId")
        forgotten = await observer.forget(session_id)
        return {"forgotten": forgotten, "sessionId": session_id}

    async def terminal_read(params: RpcParams) -> JsonValue:
        session_id_value = params.get("sessionId")
        session_id = session_id_value if isinstance(session_id_value, str) else None
        max_chars_value = params.get("maxChars", PER_SESSION_BUFFER_CHARS)
        max_chars = (
            max_chars_value
            if isinstance(max_chars_value, int) and max_chars_value > 0
            else PER_SESSION_BUFFER_CHARS
        )
        snapshot = await observer.read(session_id, max_chars=max_chars)
        if snapshot is None:
            return {"snapshot": None}
        return {"snapshot": snapshot.to_wire()}

    async def terminal_list(_: RpcParams) -> JsonValue:
        return {"sessions": await observer.list_sessions()}

    dispatcher.register("terminal.observe", terminal_observe)
    dispatcher.register("terminal.forget", terminal_forget)
    dispatcher.register("terminal.read", terminal_read)
    dispatcher.register("terminal.list", terminal_list)


def _require_str(params: RpcParams, key: str) -> str:
    value = params.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RpcError(code=INVALID_PARAMS, message=f"missing or non-string {key!r}")
    return value
