"""Chat session lifecycle.

Single-turn for v0.3: the renderer posts a `chat.send` request and the
brain streams `chat.chunk` notifications until the provider's
generator is drained, then returns a small completion response.
Multi-turn history threading and durable run state arrive in
subsequent iterations.
"""

from __future__ import annotations

from typing import Any

from thalyn_brain.provider import (
    ChatErrorChunk,
    ChatStartChunk,
    ChatStopChunk,
    ChatTextChunk,
    ChatToolCallChunk,
    ChatToolResultChunk,
    ProviderNotImplementedError,
    ProviderRegistry,
)
from thalyn_brain.rpc import (
    INVALID_PARAMS,
    Dispatcher,
    JsonValue,
    Notifier,
    RpcError,
    RpcParams,
)

CHUNK_NOTIFICATION = "chat.chunk"


def register_chat_methods(
    dispatcher: Dispatcher,
    registry: ProviderRegistry,
) -> None:
    """Wire chat handlers into the dispatcher."""

    async def chat_send(params: RpcParams, notify: Notifier) -> JsonValue:
        return await _handle_chat_send(params, notify, registry)

    dispatcher.register_streaming("chat.send", chat_send)


async def _handle_chat_send(
    params: RpcParams,
    notify: Notifier,
    registry: ProviderRegistry,
) -> JsonValue:
    session_id = _require_str(params, "sessionId")
    provider_id = _require_str(params, "providerId")
    prompt = _require_str(params, "prompt")
    system_prompt = params.get("systemPrompt")
    if system_prompt is not None and not isinstance(system_prompt, str):
        raise RpcError(code=INVALID_PARAMS, message="systemPrompt must be a string")

    try:
        provider = registry.get(provider_id)
    except ProviderNotImplementedError as exc:
        raise RpcError(code=INVALID_PARAMS, message=str(exc)) from exc

    chunk_count = 0
    final_reason = "incomplete"
    final_cost: float | None = None

    async for chunk in provider.stream_chat(prompt, system_prompt=system_prompt):
        chunk_count += 1
        wire = chunk.to_wire()
        await notify(CHUNK_NOTIFICATION, {"sessionId": session_id, "chunk": wire})

        if isinstance(chunk, ChatStopChunk):
            final_reason = chunk.reason
            final_cost = chunk.total_cost_usd
        elif isinstance(chunk, ChatErrorChunk):
            final_reason = "error"

    summary: dict[str, Any] = {
        "sessionId": session_id,
        "providerId": provider_id,
        "chunks": chunk_count,
        "reason": final_reason,
    }
    if final_cost is not None:
        summary["totalCostUsd"] = final_cost
    return summary


def _require_str(params: RpcParams, key: str) -> str:
    value = params.get(key)
    if not isinstance(value, str) or not value:
        raise RpcError(
            code=INVALID_PARAMS,
            message=f"missing or non-string '{key}'",
        )
    return value


# Keep the chunk classes referenced so static-analysis sees them as
# imported for typing, even when they're only used inside the
# isinstance checks above.
_KNOWN_CHUNKS = (
    ChatStartChunk,
    ChatTextChunk,
    ChatToolCallChunk,
    ChatToolResultChunk,
    ChatStopChunk,
    ChatErrorChunk,
)
