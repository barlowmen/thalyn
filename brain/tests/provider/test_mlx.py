"""MlxProvider tests — driven against an injected stream factory."""

from __future__ import annotations

from collections.abc import AsyncIterator

from thalyn_brain.provider.base import (
    ChatErrorChunk,
    ChatStartChunk,
    ChatStopChunk,
    ChatTextChunk,
    ReliabilityTier,
)
from thalyn_brain.provider.mlx import MlxProvider, MlxUnavailableError


async def _drain(provider: MlxProvider, prompt: str = "Hi") -> list[object]:
    return [chunk async for chunk in provider.stream_chat(prompt)]


def test_capability_profile_advertises_local_streaming() -> None:
    profile = MlxProvider().capability_profile
    assert profile.local is True
    assert profile.supports_streaming is True
    assert profile.supports_tool_use is False
    assert profile.tool_use_reliability == ReliabilityTier.LOW
    assert profile.max_context_tokens > 0


def test_default_model_and_id() -> None:
    provider = MlxProvider()
    assert provider.id == "mlx"
    assert provider.display_name == "MLX (Apple Silicon)"
    assert "Qwen3" in provider.default_model


async def test_stream_yields_start_text_stop_for_canned_tokens() -> None:
    async def fake_stream(_prompt: str) -> AsyncIterator[str]:
        for token in ["Hello", " ", "world"]:
            yield token

    async def stream_fn(prompt: str) -> AsyncIterator[str]:
        return fake_stream(prompt)

    provider = MlxProvider(stream_fn=stream_fn)
    chunks = await _drain(provider)

    kinds = [chunk.kind for chunk in chunks]  # type: ignore[attr-defined]
    assert kinds == ["start", "text", "text", "text", "stop"]
    text = "".join(c.delta for c in chunks if isinstance(c, ChatTextChunk))
    assert text == "Hello world"

    start = chunks[0]
    assert isinstance(start, ChatStartChunk)
    assert "Qwen3" in start.model
    stop = chunks[-1]
    assert isinstance(stop, ChatStopChunk)


async def test_unavailable_runtime_yields_clear_error_chunk() -> None:
    async def stream_fn(_prompt: str) -> AsyncIterator[str]:
        raise MlxUnavailableError("mlx-lm not installed in this environment")

    provider = MlxProvider(stream_fn=stream_fn)
    chunks = await _drain(provider)
    assert len(chunks) == 1
    error = chunks[0]
    assert isinstance(error, ChatErrorChunk)
    assert error.code == "mlx_unavailable"
    assert "mlx-lm" in error.message


async def test_stream_error_after_start_emits_error_chunk() -> None:
    async def fake_stream(_prompt: str) -> AsyncIterator[str]:
        yield "first"
        raise RuntimeError("ran out of tokens")

    async def stream_fn(prompt: str) -> AsyncIterator[str]:
        return fake_stream(prompt)

    provider = MlxProvider(stream_fn=stream_fn)
    chunks = await _drain(provider)
    kinds = [chunk.kind for chunk in chunks]  # type: ignore[attr-defined]
    assert kinds[0] == "start"
    assert "text" in kinds
    assert kinds[-1] == "error"
    error = chunks[-1]
    assert isinstance(error, ChatErrorChunk)
    assert error.code == "mlx_stream_error"
    assert "ran out of tokens" in error.message


async def test_history_and_system_prompt_flatten_into_prompt() -> None:
    received: list[str] = []

    async def fake_stream(_prompt: str) -> AsyncIterator[str]:
        for _ in ():
            yield ""

    async def stream_fn(prompt: str) -> AsyncIterator[str]:
        received.append(prompt)
        return fake_stream(prompt)

    provider = MlxProvider(stream_fn=stream_fn)
    async for _ in provider.stream_chat(
        "Hi",
        system_prompt="Be terse.",
        history=[
            {"role": "user", "content": "earlier"},
            {"role": "assistant", "content": "ok"},
        ],
    ):
        pass

    assert len(received) == 1
    flat = received[0]
    assert flat.startswith("system: Be terse.")
    assert "user: earlier" in flat
    assert "assistant: ok" in flat
    assert flat.endswith("user: Hi\nassistant:")
