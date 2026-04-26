"""OllamaProvider tests — driven against a fake httpx transport."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from thalyn_brain.provider import ChatChunk, ChatErrorChunk, ChatStartChunk, ChatStopChunk
from thalyn_brain.provider.base import (
    ChatTextChunk,
    ChatToolCallChunk,
    ReliabilityTier,
)
from thalyn_brain.provider.ollama import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    OllamaProvider,
)


def _ndjson(records: list[dict[str, Any]]) -> bytes:
    import json

    return ("\n".join(json.dumps(r) for r in records) + "\n").encode("utf-8")


def _client_factory_for(records: list[dict[str, Any]], *, status: int = 200) -> Any:
    """Returns a callable that yields a fresh AsyncClient backed by a
    canned MockTransport every call."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=_ndjson(records))

    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    return factory


def _bad_status_factory(status: int, body: str = "boom") -> Any:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=body.encode("utf-8"))

    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    return factory


# ---------------------------------------------------------------------------
# Capability profile + identity
# ---------------------------------------------------------------------------


def test_default_capability_profile_advertises_local_tool_use() -> None:
    profile = OllamaProvider().capability_profile
    assert profile.local is True
    assert profile.supports_tool_use is True
    assert profile.tool_use_reliability == ReliabilityTier.MEDIUM
    assert profile.supports_streaming is True
    assert profile.supports_vision is False
    assert profile.max_context_tokens > 0


def test_default_model_and_id() -> None:
    provider = OllamaProvider()
    assert provider.id == "ollama"
    assert provider.default_model == DEFAULT_MODEL
    assert provider.display_name == "Ollama (local)"


# ---------------------------------------------------------------------------
# Streaming text path
# ---------------------------------------------------------------------------


async def _drain(provider: OllamaProvider, prompt: str = "Hi") -> list[ChatChunk]:
    return [chunk async for chunk in provider.stream_chat(prompt)]


async def test_stream_emits_start_text_stop_for_simple_response() -> None:
    factory = _client_factory_for(
        [
            {
                "model": "qwen3-coder",
                "message": {"role": "assistant", "content": "Hello"},
                "done": False,
            },
            {
                "model": "qwen3-coder",
                "message": {"role": "assistant", "content": " world"},
                "done": False,
            },
            {
                "model": "qwen3-coder",
                "message": {"role": "assistant", "content": ""},
                "done": True,
                "done_reason": "stop",
            },
        ]
    )
    provider = OllamaProvider(client_factory=factory)
    chunks = await _drain(provider)

    kinds = [chunk.kind for chunk in chunks]
    assert kinds[0] == "start"
    assert "text" in kinds
    assert kinds[-1] == "stop"

    text = "".join(c.delta for c in chunks if isinstance(c, ChatTextChunk))
    assert text == "Hello world"

    start = chunks[0]
    assert isinstance(start, ChatStartChunk)
    assert start.model == "qwen3-coder"
    stop = chunks[-1]
    assert isinstance(stop, ChatStopChunk)
    assert stop.reason == "stop"


async def test_stream_normalises_a_tool_call_in_message() -> None:
    factory = _client_factory_for(
        [
            {
                "model": "qwen3-coder",
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "function": {
                                "name": "Bash",
                                "arguments": {"command": "ls"},
                            },
                        }
                    ],
                },
                "done": False,
            },
            {"model": "qwen3-coder", "message": {"content": ""}, "done": True},
        ]
    )
    provider = OllamaProvider(client_factory=factory)
    chunks = await _drain(provider)

    tool_chunks = [c for c in chunks if isinstance(c, ChatToolCallChunk)]
    assert len(tool_chunks) == 1
    call = tool_chunks[0]
    assert call.call_id == "call_1"
    assert call.tool == "Bash"
    assert call.input == {"command": "ls"}


async def test_stream_synthesises_call_id_when_missing() -> None:
    factory = _client_factory_for(
        [
            {
                "model": "qwen3-coder",
                "created_at": "2026-04-26T00:00:00Z",
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{"function": {"name": "Search", "arguments": {"q": "x"}}}],
                },
                "done": False,
            },
            {"model": "qwen3-coder", "message": {"content": ""}, "done": True},
        ]
    )
    provider = OllamaProvider(client_factory=factory)
    chunks = await _drain(provider)
    tool_chunks = [c for c in chunks if isinstance(c, ChatToolCallChunk)]
    assert len(tool_chunks) == 1
    assert tool_chunks[0].call_id.startswith("call_")
    assert tool_chunks[0].input == {"q": "x"}


async def test_stream_parses_string_arguments_as_json() -> None:
    factory = _client_factory_for(
        [
            {
                "model": "qwen3-coder",
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_2",
                            "function": {
                                "name": "Bash",
                                "arguments": '{"command": "ls"}',
                            },
                        }
                    ],
                },
                "done": True,
            }
        ]
    )
    provider = OllamaProvider(client_factory=factory)
    chunks = await _drain(provider)
    tool_chunks = [c for c in chunks if isinstance(c, ChatToolCallChunk)]
    assert tool_chunks[0].input == {"command": "ls"}


async def test_stream_yields_error_on_non_200() -> None:
    factory = _bad_status_factory(503, body="ollama unavailable")
    provider = OllamaProvider(client_factory=factory)
    chunks = await _drain(provider)
    error_chunks = [c for c in chunks if isinstance(c, ChatErrorChunk)]
    assert len(error_chunks) == 1
    assert "503" in error_chunks[0].message
    assert error_chunks[0].code == "503"


async def test_stream_yields_error_on_transport_failure() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    provider = OllamaProvider(client_factory=factory)
    chunks = await _drain(provider)
    error_chunks = [c for c in chunks if isinstance(c, ChatErrorChunk)]
    assert len(error_chunks) == 1
    assert "transport" in (error_chunks[0].code or "")


@pytest.mark.parametrize(
    "system_prompt,history,expected_messages",
    [
        (
            None,
            None,
            [{"role": "user", "content": "Hi"}],
        ),
        (
            "Be terse.",
            None,
            [
                {"role": "system", "content": "Be terse."},
                {"role": "user", "content": "Hi"},
            ],
        ),
        (
            None,
            [{"role": "user", "content": "earlier"}, {"role": "assistant", "content": "ok"}],
            [
                {"role": "user", "content": "earlier"},
                {"role": "assistant", "content": "ok"},
                {"role": "user", "content": "Hi"},
            ],
        ),
    ],
)
async def test_request_payload_carries_system_and_history(
    system_prompt: str | None,
    history: list[dict[str, Any]] | None,
    expected_messages: list[dict[str, Any]],
) -> None:
    captured: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        captured.append(json.loads(request.content))
        return httpx.Response(
            200,
            content=_ndjson([{"model": "qwen3-coder", "message": {"content": ""}, "done": True}]),
        )

    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    provider = OllamaProvider(client_factory=factory)
    async for _ in provider.stream_chat("Hi", system_prompt=system_prompt, history=history):
        pass

    assert len(captured) == 1
    payload = captured[0]
    assert payload["model"] == DEFAULT_MODEL
    assert payload["stream"] is True
    assert payload["messages"] == expected_messages


async def test_provider_targets_default_base_url_by_default() -> None:
    captured_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_urls.append(str(request.url))
        return httpx.Response(
            200,
            content=_ndjson([{"model": "qwen3-coder", "message": {"content": ""}, "done": True}]),
        )

    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    provider = OllamaProvider(client_factory=factory)
    async for _ in provider.stream_chat("Hi"):
        pass

    assert captured_urls == [f"{DEFAULT_BASE_URL}/api/chat"]
