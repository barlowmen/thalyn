"""Inline-suggest service + JSON-RPC binding."""

from __future__ import annotations

from typing import Any

import pytest
from thalyn_brain.inline import (
    MAX_SUGGESTION_CHARS,
    InlineSuggestion,
    build_system_prompt,
    build_user_prompt,
    suggest,
)
from thalyn_brain.inline_rpc import register_inline_methods
from thalyn_brain.provider import AnthropicProvider, ProviderRegistry
from thalyn_brain.rpc import Dispatcher

from tests.provider._fake_sdk import factory_for, result_message, text_message


def _registry_with(provider: AnthropicProvider) -> ProviderRegistry:
    registry = ProviderRegistry()
    registry._providers["anthropic"] = provider
    return registry


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------


def test_system_prompt_includes_language_when_provided() -> None:
    assert "language: python" in build_system_prompt("python")


def test_system_prompt_omits_language_when_blank() -> None:
    out = build_system_prompt("")
    assert "(language" not in out


def test_user_prompt_marks_cursor_with_anchor() -> None:
    body = build_user_prompt("foo(", ")")
    assert "foo(<CURSOR/>)" in body


# ---------------------------------------------------------------------------
# suggest()
# ---------------------------------------------------------------------------


async def test_suggest_concatenates_text_chunks_into_one_string() -> None:
    _, factory = factory_for(
        [
            text_message("hello, "),
            text_message("world"),
            result_message(),
        ]
    )
    provider = AnthropicProvider(client_factory=factory)

    out = await suggest(
        provider=provider,
        provider_id="anthropic",
        request_id="rq_1",
        prefix="print(",
        suffix=")",
        language="python",
    )

    assert isinstance(out, InlineSuggestion)
    assert out.suggestion == "hello, world"
    assert out.request_id == "rq_1"
    assert out.provider_id == "anthropic"
    assert out.completed_at_ms >= out.requested_at_ms
    assert out.truncated is False


async def test_suggest_strips_trailing_code_fence() -> None:
    _, factory = factory_for(
        [
            text_message("```python\n"),
            text_message("print('hi')\n"),
            text_message("```"),
            result_message(),
        ]
    )
    provider = AnthropicProvider(client_factory=factory)

    out = await suggest(
        provider=provider,
        provider_id="anthropic",
        request_id="rq",
        prefix="",
        language="python",
    )
    # The provider returned a fenced block; the trim should leave the
    # raw code (we accept the fence as a stop token, so anything after
    # it is dropped).
    assert "```" not in out.suggestion


async def test_suggest_respects_max_char_budget() -> None:
    big = "x" * (MAX_SUGGESTION_CHARS + 50)
    _, factory = factory_for(
        [
            text_message(big),
            result_message(),
        ]
    )
    provider = AnthropicProvider(client_factory=factory)

    out = await suggest(
        provider=provider,
        provider_id="anthropic",
        request_id="rq",
        prefix="",
    )
    assert out.truncated is True
    assert len(out.suggestion) <= MAX_SUGGESTION_CHARS


async def test_suggest_stops_on_blank_line_break() -> None:
    _, factory = factory_for(
        [
            text_message("first line"),
            text_message("\n\n"),
            text_message("commentary that should not appear"),
            result_message(),
        ]
    )
    provider = AnthropicProvider(client_factory=factory)

    out = await suggest(
        provider=provider,
        provider_id="anthropic",
        request_id="rq",
        prefix="",
    )
    assert out.suggestion == "first line"


# ---------------------------------------------------------------------------
# JSON-RPC
# ---------------------------------------------------------------------------


async def test_inline_suggest_dispatcher_round_trip() -> None:
    _, factory = factory_for(
        [
            text_message("answer = 42"),
            result_message(),
        ]
    )
    provider = AnthropicProvider(client_factory=factory)
    registry = _registry_with(provider)
    dispatcher = Dispatcher()
    register_inline_methods(dispatcher, registry)

    async def notify(method: str, params: Any) -> None:
        del method, params

    response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "inline.suggest",
            "params": {
                "providerId": "anthropic",
                "prefix": "answer = ",
                "language": "python",
                "requestId": "rq_42",
            },
        },
        notify,
    )
    assert response is not None
    assert response["result"]["suggestion"] == "answer = 42"
    assert response["result"]["requestId"] == "rq_42"
    assert response["result"]["providerId"] == "anthropic"


async def test_inline_suggest_generates_request_id_when_missing() -> None:
    _, factory = factory_for(
        [
            text_message("x"),
            result_message(),
        ]
    )
    provider = AnthropicProvider(client_factory=factory)
    registry = _registry_with(provider)
    dispatcher = Dispatcher()
    register_inline_methods(dispatcher, registry)

    async def notify(method: str, params: Any) -> None:
        del method, params

    response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "inline.suggest",
            "params": {
                "providerId": "anthropic",
                "prefix": "let z = ",
                "language": "typescript",
            },
        },
        notify,
    )
    assert response is not None
    rid = response["result"]["requestId"]
    assert isinstance(rid, str)
    assert rid.startswith("inline_")


async def test_inline_suggest_rejects_unknown_provider() -> None:
    registry = ProviderRegistry()
    dispatcher = Dispatcher()
    register_inline_methods(dispatcher, registry)

    async def notify(method: str, params: Any) -> None:
        del method, params

    response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "inline.suggest",
            "params": {"providerId": "nope", "prefix": "x"},
        },
        notify,
    )
    assert response is not None
    assert "unknown provider" in response["error"]["message"]


@pytest.mark.parametrize("payload", [{}, {"providerId": "anthropic"}])
async def test_inline_suggest_validates_required_params(payload: dict[str, Any]) -> None:
    registry = ProviderRegistry()
    dispatcher = Dispatcher()
    register_inline_methods(dispatcher, registry)

    async def notify(method: str, params: Any) -> None:
        del method, params

    response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "inline.suggest",
            "params": payload,
        },
        notify,
    )
    assert response is not None
    assert "error" in response
