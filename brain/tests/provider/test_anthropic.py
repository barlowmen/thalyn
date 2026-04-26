"""Tests for the Anthropic provider's translation layer."""

from __future__ import annotations

from typing import Any

from thalyn_brain.provider import (
    AnthropicProvider,
    Capability,
    ChatErrorChunk,
    ChatStartChunk,
    ChatStopChunk,
    ChatTextChunk,
    ChatToolCallChunk,
    ChatToolResultChunk,
)

from tests.provider._fake_sdk import (
    factory_for,
    result_message,
    text_message,
    tool_call_message,
    tool_result_message,
)


async def _drain(provider: AnthropicProvider, prompt: str) -> list[Any]:
    chunks: list[Any] = []
    async for chunk in provider.stream_chat(prompt):
        chunks.append(chunk)
    return chunks


async def test_simple_text_response_emits_start_text_stop() -> None:
    fake, factory = factory_for(
        [
            text_message("Hello, "),
            text_message("world."),
            result_message(total_cost_usd=0.0001),
        ]
    )
    provider = AnthropicProvider(client_factory=factory)

    chunks = await _drain(provider, "Hi there")

    assert fake.queries == ["Hi there"]
    assert isinstance(chunks[0], ChatStartChunk)
    assert chunks[0].model == "claude-sonnet-4-6"
    assert isinstance(chunks[1], ChatTextChunk)
    assert chunks[1].delta == "Hello, "
    assert isinstance(chunks[2], ChatTextChunk)
    assert chunks[2].delta == "world."
    assert isinstance(chunks[3], ChatStopChunk)
    assert chunks[3].reason == "end_turn"
    assert chunks[3].total_cost_usd == 0.0001
    assert len(chunks) == 4


async def test_tool_call_then_result_translates_to_chunks() -> None:
    fake, factory = factory_for(
        [
            tool_call_message(call_id="call_42", name="Bash", input_={"command": "ls"}),
            tool_result_message(call_id="call_42", output="README.md\n"),
            text_message("Done."),
            result_message(),
        ]
    )
    provider = AnthropicProvider(client_factory=factory)

    chunks = await _drain(provider, "List files")

    kinds = [chunk.kind for chunk in chunks]
    assert kinds == ["start", "tool_call", "tool_result", "text", "stop"]

    tool_call = chunks[1]
    assert isinstance(tool_call, ChatToolCallChunk)
    assert tool_call.call_id == "call_42"
    assert tool_call.tool == "Bash"
    assert tool_call.input == {"command": "ls"}

    tool_result = chunks[2]
    assert isinstance(tool_result, ChatToolResultChunk)
    assert tool_result.call_id == "call_42"
    assert tool_result.output == "README.md\n"
    assert tool_result.is_error is False

    assert isinstance(fake.queries, list)
    assert fake.queries == ["List files"]


async def test_unexpected_error_is_routed_to_error_chunk() -> None:
    class ExplodingFactory:
        def __call__(self, options: Any) -> Any:
            raise RuntimeError("kaboom")

    provider = AnthropicProvider(client_factory=ExplodingFactory())
    chunks = await _drain(provider, "Hello")

    assert isinstance(chunks[0], ChatStartChunk)
    error = chunks[1]
    assert isinstance(error, ChatErrorChunk)
    assert "kaboom" in error.message
    assert error.code == "RuntimeError"


async def test_capability_profile_advertises_high_tool_use() -> None:
    provider = AnthropicProvider()
    profile = provider.capability_profile
    assert profile.supports_tool_use is True
    assert profile.supports_streaming is True
    assert profile.supports_vision is True
    assert provider.supports(Capability.TOOL_USE)
    assert profile.local is False
