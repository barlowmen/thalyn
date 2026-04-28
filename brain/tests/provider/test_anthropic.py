"""Tests for the Anthropic provider's translation layer."""

from __future__ import annotations

from typing import Any

import pytest
from thalyn_brain.provider import (
    AnthropicApiAuth,
    AnthropicProvider,
    AuthBackendKind,
    Capability,
    ChatErrorChunk,
    ChatStartChunk,
    ChatStopChunk,
    ChatTextChunk,
    ChatToolCallChunk,
    ChatToolResultChunk,
    ClaudeSubscriptionAuth,
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


# ---------------------------------------------------------------------------
# Auth-backend composition (ADR-0020)
# ---------------------------------------------------------------------------


async def test_subscription_auth_leaves_api_key_unset_in_sdk_env() -> None:
    """Subscription auth: token() returns None, ANTHROPIC_API_KEY stays
    unset, and the bundled CLI's stored OAuth flows through."""
    fake, factory = factory_for([text_message("ok"), result_message()])
    subscription = ClaudeSubscriptionAuth(cli_locator=lambda: None)
    provider = AnthropicProvider(client_factory=factory, auth_backend=subscription)
    assert provider.auth_backend.kind == AuthBackendKind.CLAUDE_SUBSCRIPTION

    await _drain(provider, "hi")

    assert fake.options is not None
    env = dict(fake.options.env or {})
    assert env.get("ANTHROPIC_MODEL") == "claude-sonnet-4-6"
    assert "ANTHROPIC_API_KEY" not in env


async def test_api_key_auth_injects_key_into_sdk_env() -> None:
    """API-key auth: token() returns the key, ANTHROPIC_API_KEY is set."""
    fake, factory = factory_for([text_message("ok"), result_message()])
    api_auth = AnthropicApiAuth(source="sk-test-from-keychain")
    provider = AnthropicProvider(client_factory=factory, auth_backend=api_auth)
    assert provider.auth_backend.kind == AuthBackendKind.ANTHROPIC_API

    await _drain(provider, "hi")

    assert fake.options is not None
    env = dict(fake.options.env or {})
    assert env.get("ANTHROPIC_API_KEY") == "sk-test-from-keychain"
    assert env.get("ANTHROPIC_MODEL") == "claude-sonnet-4-6"


async def test_default_constructor_uses_api_key_auth_for_v1_compat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No auth_backend → AnthropicApiAuth() → reads ANTHROPIC_API_KEY env.
    Preserves the v1 spawn-env path so existing brain installs keep
    working."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-from-env")
    fake, factory = factory_for([text_message("ok"), result_message()])
    provider = AnthropicProvider(client_factory=factory)
    assert provider.auth_backend.kind == AuthBackendKind.ANTHROPIC_API

    await _drain(provider, "hi")

    assert fake.options is not None
    env = dict(fake.options.env or {})
    assert env.get("ANTHROPIC_API_KEY") == "sk-from-env"


async def test_subscription_auth_preserves_system_prompt() -> None:
    """The system_prompt threading is independent of the auth backend."""
    fake, factory = factory_for([text_message("ok"), result_message()])
    subscription = ClaudeSubscriptionAuth(cli_locator=lambda: None)
    provider = AnthropicProvider(client_factory=factory, auth_backend=subscription)

    async for _ in provider.stream_chat("hi", system_prompt="You are Thalyn."):
        pass

    assert fake.options is not None
    assert fake.options.system_prompt == "You are Thalyn."
    env = dict(fake.options.env or {})
    assert "ANTHROPIC_API_KEY" not in env
