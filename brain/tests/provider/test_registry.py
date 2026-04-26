"""Tests for the provider registry."""

from __future__ import annotations

import pytest
from thalyn_brain.provider import (
    Capability,
    ProviderKind,
    ProviderNotImplementedError,
    build_registry,
)


def test_registry_lists_anthropic_first_with_local_options_enabled() -> None:
    registry = build_registry()
    metas = registry.list_meta(configured={"anthropic": True})

    assert metas[0].id == "anthropic"
    assert metas[0].enabled is True
    assert metas[0].configured is True
    assert metas[0].kind is ProviderKind.ANTHROPIC

    other_ids = {meta.id for meta in metas[1:]}
    assert other_ids == {"openai_compat", "ollama", "llama_cpp", "mlx"}

    enabled_ids = {meta.id for meta in metas if meta.enabled}
    # Anthropic plus the v0.10 local options that have real adapters.
    assert enabled_ids == {"anthropic", "ollama"}
    disabled_ids = {meta.id for meta in metas if not meta.enabled}
    assert disabled_ids == {"openai_compat", "llama_cpp", "mlx"}


def test_registry_marks_unconfigured_anthropic() -> None:
    registry = build_registry()
    metas = registry.list_meta()
    anthropic = next(meta for meta in metas if meta.id == "anthropic")
    assert anthropic.configured is False


def test_meta_to_wire_uses_camel_case() -> None:
    registry = build_registry()
    meta = next(m for m in registry.list_meta() if m.id == "anthropic")
    wire = meta.to_wire()
    assert wire["displayName"].startswith("Anthropic")
    assert wire["defaultModel"] == "claude-sonnet-4-6"
    profile = wire["capabilityProfile"]
    assert "maxContextTokens" in profile
    assert "supportsToolUse" in profile
    assert "toolUseReliability" in profile


async def test_placeholder_provider_streaming_raises_not_implemented() -> None:
    """The remaining placeholders (llama_cpp, mlx) still error when
    streamed — only the Anthropic and Ollama adapters are real in
    v0.10."""
    registry = build_registry()
    llama = registry.get("llama_cpp")
    with pytest.raises(ProviderNotImplementedError):
        async for _ in llama.stream_chat("hi"):
            pass


def test_placeholder_supports_returns_false_for_remaining_stubs() -> None:
    registry = build_registry()
    llama = registry.get("llama_cpp")
    assert llama.supports(Capability.STREAMING) is False
    assert llama.supports(Capability.TOOL_USE) is False


def test_ollama_provider_advertises_real_capabilities() -> None:
    registry = build_registry()
    ollama = registry.get("ollama")
    profile = ollama.capability_profile
    assert profile.supports_streaming is True
    assert profile.supports_tool_use is True
    assert profile.local is True
    assert ollama.supports(Capability.STREAMING) is True
