"""Contract tests every provider must satisfy.

Drives a provider through the recorded fixtures and asserts the
universal invariants — every successful response starts with a
`start` chunk, ends with a `stop` chunk, and produces only the
chunk shapes declared in the protocol. Fixtures are JSON files under
`fixtures/`; adding a provider is "ship a few fixtures + run this".
"""

from __future__ import annotations

import pytest
from thalyn_brain.provider import (
    AnthropicProvider,
    ChatChunk,
    ChatErrorChunk,
    ChatStartChunk,
    ChatStopChunk,
    ChatTextChunk,
    ChatToolCallChunk,
    ChatToolResultChunk,
)

from tests.provider._fake_sdk import factory_for
from tests.provider._fixtures import load

CHUNK_KINDS = {
    ChatStartChunk: "start",
    ChatTextChunk: "text",
    ChatToolCallChunk: "tool_call",
    ChatToolResultChunk: "tool_result",
    ChatStopChunk: "stop",
    ChatErrorChunk: "error",
}


async def _drain_with_fixture(fixture: str) -> list[ChatChunk]:
    _fake, factory = factory_for(load(fixture))
    provider = AnthropicProvider(client_factory=factory)
    chunks: list[ChatChunk] = []
    async for chunk in provider.stream_chat("the prompt"):
        chunks.append(chunk)
    return chunks


@pytest.mark.parametrize(
    "fixture",
    ["simple_text", "bash_tool", "tool_error"],
)
async def test_fixture_drives_a_well_shaped_chunk_stream(fixture: str) -> None:
    chunks = await _drain_with_fixture(fixture)

    assert chunks, "fixture must produce at least one chunk"
    assert isinstance(chunks[0], ChatStartChunk), "first chunk is start"
    assert isinstance(chunks[-1], ChatStopChunk | ChatErrorChunk), "last chunk is terminal"

    # Every chunk is a known shape.
    for chunk in chunks:
        assert type(chunk) in CHUNK_KINDS

    # Tool results must reference a call that was emitted.
    seen_call_ids: set[str] = set()
    for chunk in chunks:
        if isinstance(chunk, ChatToolCallChunk):
            seen_call_ids.add(chunk.call_id)
        elif isinstance(chunk, ChatToolResultChunk):
            assert chunk.call_id in seen_call_ids, (
                f"tool_result references unseen call {chunk.call_id}"
            )


async def test_simple_text_fixture_round_trips_text_deltas() -> None:
    chunks = await _drain_with_fixture("simple_text")
    text_chunks = [c for c in chunks if isinstance(c, ChatTextChunk)]
    assert "".join(c.delta for c in text_chunks) == "Hello, world."

    stop = next(c for c in chunks if isinstance(c, ChatStopChunk))
    assert stop.total_cost_usd == 0.0001


async def test_tool_error_fixture_propagates_is_error_flag() -> None:
    chunks = await _drain_with_fixture("tool_error")
    result = next(c for c in chunks if isinstance(c, ChatToolResultChunk))
    assert result.is_error is True
    assert result.output == "exit code 1"


async def test_to_wire_round_trips_through_json() -> None:
    """Every chunk shape's wire form is JSON-serialisable end-to-end."""
    import json

    chunks = await _drain_with_fixture("bash_tool")
    for chunk in chunks:
        wire = chunk.to_wire()
        # Should round-trip through json without losing structure.
        rehydrated = json.loads(json.dumps(wire))
        assert rehydrated == wire
        assert "kind" in rehydrated
