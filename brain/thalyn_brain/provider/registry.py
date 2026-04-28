"""Built-in provider registry.

Mirrors `src-tauri/src/provider/registry.rs`. v0.3 enables the
Anthropic adapter; the OpenAI-compatible / Ollama / llama.cpp / MLX
slots are placeholders so users see what's coming.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from thalyn_brain.provider.anthropic import DEFAULT_MODEL, AnthropicProvider
from thalyn_brain.provider.auth import AuthBackend
from thalyn_brain.provider.base import (
    Capability,
    CapabilityProfile,
    ChatChunk,
    ChatErrorChunk,
    LlmProvider,
    ProviderKind,
    ProviderMeta,
    ProviderNotImplementedError,
    ReliabilityTier,
)
from thalyn_brain.provider.mlx import (
    DEFAULT_MODEL as MLX_DEFAULT_MODEL,
)
from thalyn_brain.provider.mlx import (
    MlxProvider,
)
from thalyn_brain.provider.ollama import (
    DEFAULT_MODEL as OLLAMA_DEFAULT_MODEL,
)
from thalyn_brain.provider.ollama import (
    OllamaProvider,
)


class _PlaceholderProvider:
    """Surfaces in the listing but cannot stream."""

    def __init__(self, id: str, display_name: str, kind: ProviderKind) -> None:
        self._id = id
        self._display_name = display_name
        self._kind = kind
        self._profile = CapabilityProfile(
            max_context_tokens=0,
            supports_tool_use=False,
            tool_use_reliability=ReliabilityTier.UNKNOWN,
            supports_vision=False,
            supports_streaming=False,
            local=kind in {ProviderKind.OLLAMA, ProviderKind.LLAMA_CPP, ProviderKind.MLX},
        )

    @property
    def id(self) -> str:
        return self._id

    @property
    def display_name(self) -> str:
        return self._display_name

    @property
    def capability_profile(self) -> CapabilityProfile:
        return self._profile

    @property
    def default_model(self) -> str:
        return ""

    def supports(self, capability: Capability) -> bool:
        return self._profile.supports(capability)

    def stream_chat(
        self,
        prompt: str,
        *,
        history: list[dict[str, Any]] | None = None,
        system_prompt: str | None = None,
    ) -> AsyncIterator[ChatChunk]:
        del prompt, history, system_prompt
        return _placeholder_stream(self._id)


async def _placeholder_stream(provider_id: str) -> AsyncIterator[ChatChunk]:
    raise ProviderNotImplementedError(f"provider {provider_id} is not implemented in v0.3")
    # The yield below is never reached, but its presence forces Python
    # to treat this function as an async generator so the caller can
    # use it with `async for`. mypy's flow analysis is told to ignore.
    yield ChatErrorChunk(message="unreachable")  # type: ignore[unreachable]


def builtin_providers(*, anthropic_auth: AuthBackend | None = None) -> list[LlmProvider]:
    return [
        AnthropicProvider(auth_backend=anthropic_auth),
        _PlaceholderProvider(
            "openai_compat",
            "OpenAI-compatible endpoint",
            ProviderKind.OPENAI_COMPATIBLE,
        ),
        OllamaProvider(),
        _PlaceholderProvider("llama_cpp", "llama.cpp (local)", ProviderKind.LLAMA_CPP),
        MlxProvider(),
    ]


class ProviderRegistry:
    """Lookup + metadata for the built-in providers."""

    def __init__(self, providers: list[LlmProvider] | None = None) -> None:
        self._providers: dict[str, LlmProvider] = {
            provider.id: provider for provider in (providers or builtin_providers())
        }

    def list_meta(self, *, configured: dict[str, bool] | None = None) -> list[ProviderMeta]:
        configured = configured or {}
        enabled_ids = {"anthropic", "ollama", "mlx"}
        out: list[ProviderMeta] = []
        for provider in self._providers.values():
            kind = _kind_for(provider.id)
            default_model = ""
            if provider.id == "anthropic":
                default_model = DEFAULT_MODEL
            elif provider.id == "ollama":
                default_model = OLLAMA_DEFAULT_MODEL
            elif provider.id == "mlx":
                default_model = MLX_DEFAULT_MODEL
            out.append(
                ProviderMeta(
                    id=provider.id,
                    display_name=provider.display_name,
                    kind=kind,
                    default_model=default_model,
                    capability_profile=provider.capability_profile,
                    configured=configured.get(provider.id, False),
                    enabled=provider.id in enabled_ids,
                )
            )
        return out

    def get(self, provider_id: str) -> LlmProvider:
        try:
            return self._providers[provider_id]
        except KeyError as exc:
            raise ProviderNotImplementedError(f"unknown provider: {provider_id}") from exc


def build_registry(*, anthropic_auth: AuthBackend | None = None) -> ProviderRegistry:
    return ProviderRegistry(builtin_providers(anthropic_auth=anthropic_auth))


def _kind_for(provider_id: str) -> ProviderKind:
    match provider_id:
        case "anthropic":
            return ProviderKind.ANTHROPIC
        case "openai_compat":
            return ProviderKind.OPENAI_COMPATIBLE
        case "ollama":
            return ProviderKind.OLLAMA
        case "llama_cpp":
            return ProviderKind.LLAMA_CPP
        case "mlx":
            return ProviderKind.MLX
        case _:
            return ProviderKind.OPENAI_COMPATIBLE
