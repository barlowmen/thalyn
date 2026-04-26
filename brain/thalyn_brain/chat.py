"""Chat session lifecycle.

Each chat turn now flows through the LangGraph orchestrator
(`thalyn_brain.orchestration`). The handler routes the user prompt
through plan → execute → critic → respond and forwards the
graph's notifications (chat.chunk, run.plan_update, run.action_log,
run.status) on through the JSON-RPC dispatcher.

The wire shape stays compatible with v0.3 — `chat.send` is still the
entry point and still emits `chat.chunk` for streamed tokens — but
it gains the run-lifecycle vocabulary the renderer's inspector will
subscribe to next.
"""

from __future__ import annotations

from typing import Any

from thalyn_brain.orchestration import Runner
from thalyn_brain.provider import ProviderNotImplementedError, ProviderRegistry
from thalyn_brain.rpc import (
    INVALID_PARAMS,
    Dispatcher,
    JsonValue,
    Notifier,
    RpcError,
    RpcParams,
)


def register_chat_methods(
    dispatcher: Dispatcher,
    registry: ProviderRegistry,
    *,
    runner: Runner | None = None,
) -> None:
    """Wire chat handlers into the dispatcher.

    A `Runner` may be passed in for tests that want to swap the
    checkpointer factory; production callers can omit it and use the
    default in-memory checkpointer.
    """
    bound_runner = runner or Runner(registry)

    async def chat_send(params: RpcParams, notify: Notifier) -> JsonValue:
        return await _handle_chat_send(params, notify, bound_runner)

    dispatcher.register_streaming("chat.send", chat_send)


async def _handle_chat_send(
    params: RpcParams,
    notify: Notifier,
    runner: Runner,
) -> JsonValue:
    session_id = _require_str(params, "sessionId")
    provider_id = _require_str(params, "providerId")
    prompt = _require_str(params, "prompt")

    try:
        result = await runner.run(
            session_id=session_id,
            provider_id=provider_id,
            prompt=prompt,
            notify=notify,
        )
    except ProviderNotImplementedError as exc:
        raise RpcError(code=INVALID_PARAMS, message=str(exc)) from exc

    summary: dict[str, Any] = {
        "sessionId": result.session_id,
        "providerId": result.provider_id,
        "runId": result.run_id,
        "status": result.status,
        "actionLogSize": result.action_log_size,
    }
    if result.plan is not None:
        summary["plan"] = result.plan
    return summary


def _require_str(params: RpcParams, key: str) -> str:
    value = params.get(key)
    if not isinstance(value, str) or not value:
        raise RpcError(
            code=INVALID_PARAMS,
            message=f"missing or non-string '{key}'",
        )
    return value
