"""Tests for the provider registry."""

from __future__ import annotations

import pytest
from thalyn_brain.provider import (
    Capability,
    ProviderKind,
    ProviderNotImplementedError,
    build_registry,
)


def test_registry_lists_anthropic_first_with_others_disabled() -> None:
    registry = build_registry()
    metas = registry.list_meta(configured={"anthropic": True})

    assert metas[0].id == "anthropic"
    assert metas[0].enabled is True
    assert metas[0].configured is True
    assert metas[0].kind is ProviderKind.ANTHROPIC

    placeholder_ids = {meta.id for meta in metas[1:]}
    assert placeholder_ids == {"openai_compat", "ollama", "llama_cpp", "mlx"}
    for meta in metas[1:]:
        assert meta.enabled is False


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
    registry = build_registry()
    ollama = registry.get("ollama")
    with pytest.raises(ProviderNotImplementedError):
        async for _ in ollama.stream_chat("hi"):
            pass


def test_placeholder_supports_returns_false() -> None:
    registry = build_registry()
    ollama = registry.get("ollama")
    assert ollama.supports(Capability.STREAMING) is False
    assert ollama.supports(Capability.TOOL_USE) is False
