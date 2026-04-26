"""Ollama provider — local-model streaming via the Ollama HTTP API.

Ollama exposes an OpenAI-shaped chat API at ``/api/chat`` with a
streaming NDJSON response. We translate that into the
``LlmProvider`` Protocol's chunk vocabulary so the orchestrator
sees the same shape regardless of provider.

Tool-call normalisation: Ollama emits each tool call as a single
JSON object inside a streamed message (no incremental
arg-streaming). We surface each one as one ``ChatToolCallChunk``;
tool *results* still arrive through the orchestrator's existing
tool-execution path (the agent loop appends them to history),
matching the Anthropic adapter's behaviour.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx

from thalyn_brain.provider.base import (
    Capability,
    CapabilityProfile,
    ChatChunk,
    ChatErrorChunk,
    ChatStartChunk,
    ChatStopChunk,
    ChatTextChunk,
    ChatToolCallChunk,
    ReliabilityTier,
)

DEFAULT_BASE_URL = "http://localhost:11434"
DEFAULT_MODEL = "qwen3-coder"
DEFAULT_CONTEXT_TOKENS = 32_768
"""Conservative floor — Qwen3-Coder ships with 32 k context by default."""


ClientFactory = Callable[[], httpx.AsyncClient]


def _default_client_factory() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(60.0, read=300.0))


class OllamaProvider:
    """Concrete ``LlmProvider`` for a local Ollama daemon."""

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_MODEL,
        client_factory: ClientFactory | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._client_factory = client_factory or _default_client_factory

    @property
    def id(self) -> str:
        return "ollama"

    @property
    def display_name(self) -> str:
        return "Ollama (local)"

    @property
    def capability_profile(self) -> CapabilityProfile:
        return CapabilityProfile(
            max_context_tokens=DEFAULT_CONTEXT_TOKENS,
            supports_tool_use=True,
            tool_use_reliability=ReliabilityTier.MEDIUM,
            supports_vision=False,
            supports_streaming=True,
            local=True,
        )

    @property
    def default_model(self) -> str:
        return self._model

    def supports(self, capability: Capability) -> bool:
        return self.capability_profile.supports(capability)

    async def stream_chat(
        self,
        prompt: str,
        *,
        history: list[dict[str, Any]] | None = None,
        system_prompt: str | None = None,
    ) -> AsyncIterator[ChatChunk]:
        async for chunk in _stream(
            self._client_factory,
            self._base_url,
            self._model,
            prompt=prompt,
            history=history,
            system_prompt=system_prompt,
        ):
            yield chunk


async def _stream(
    client_factory: ClientFactory,
    base_url: str,
    model: str,
    *,
    prompt: str,
    history: list[dict[str, Any]] | None,
    system_prompt: str | None,
) -> AsyncIterator[ChatChunk]:
    messages = _build_messages(prompt, history=history, system_prompt=system_prompt)
    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
    }
    started = False
    try:
        async with client_factory() as client:
            async with client.stream("POST", f"{base_url}/api/chat", json=payload) as response:
                if response.status_code != 200:
                    body = await response.aread()
                    snippet = body.decode("utf-8", errors="replace")[:200]
                    yield ChatErrorChunk(
                        message=f"ollama returned {response.status_code}: {snippet}",
                        code=str(response.status_code),
                    )
                    return
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(record, dict):
                        continue
                    if not started:
                        started = True
                        yield ChatStartChunk(model=str(record.get("model") or model))
                    async for sub in _translate_record(record):
                        yield sub
                    if record.get("done"):
                        return
    except httpx.HTTPError as exc:
        yield ChatErrorChunk(message=f"ollama transport error: {exc}", code="transport")


async def _translate_record(record: dict[str, Any]) -> AsyncIterator[ChatChunk]:
    message = record.get("message") or {}
    if not isinstance(message, dict):
        message = {}

    content = message.get("content")
    if isinstance(content, str) and content:
        yield ChatTextChunk(delta=content)

    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, list):
        for index, raw in enumerate(tool_calls):
            normalised = _normalise_tool_call(raw, index=index, record=record)
            if normalised is not None:
                yield normalised

    if record.get("done"):
        reason = str(record.get("done_reason") or "stop")
        yield ChatStopChunk(reason=reason)


def _normalise_tool_call(
    raw: Any,
    *,
    index: int,
    record: dict[str, Any],
) -> ChatToolCallChunk | None:
    if not isinstance(raw, dict):
        return None
    function = raw.get("function") or raw
    if not isinstance(function, dict):
        return None
    name = function.get("name")
    if not isinstance(name, str) or not name:
        return None
    arguments = function.get("arguments") or {}
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            arguments = {"_raw": arguments}
    if not isinstance(arguments, dict):
        arguments = {"_value": arguments}
    call_id = raw.get("id") or function.get("id")
    if not isinstance(call_id, str) or not call_id:
        # Synthesise a stable id from the message timestamp so two
        # calls in the same record don't collide.
        token = record.get("created_at") or record.get("model") or "ollama"
        call_id = f"call_{token}_{index}"
    return ChatToolCallChunk(call_id=call_id, tool=name, input=arguments)


def _build_messages(
    prompt: str,
    *,
    history: list[dict[str, Any]] | None,
    system_prompt: str | None,
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    if history:
        for entry in history:
            role = entry.get("role")
            content = entry.get("content")
            if isinstance(role, str) and isinstance(content, str):
                messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": prompt})
    return messages


__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_CONTEXT_TOKENS",
    "DEFAULT_MODEL",
    "OllamaProvider",
]
