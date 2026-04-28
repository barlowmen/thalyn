"""In-memory registry of auth-backend instances for the brain.

Holds one instance per ``AuthBackendKind`` plus a single "active" kind
that drives which backend the Anthropic provider composes when it talks
to the SDK. The registry is mutated through ``set_active`` from the
``auth.set`` IPC handler; instances live for the brain's lifetime so the
probe cache (e.g. ``ClaudeSubscriptionAuth``'s) survives across calls.

The store record class ``AuthBackendRecord`` (in ``auth_backends.py``)
is the *persistence* shape; this module is the *runtime* shape. The two
are separate so the wizard can probe / activate without touching SQLite.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from thalyn_brain.provider.auth import (
    AuthBackend,
    AuthBackendError,
    AuthBackendKind,
    AuthProbeResult,
)
from thalyn_brain.provider.auth_anthropic import (
    AnthropicApiAuth,
    ClaudeSubscriptionAuth,
)
from thalyn_brain.provider.auth_local import (
    LlamaCppAuth,
    MlxAuth,
    OllamaAuth,
)
from thalyn_brain.provider.auth_openai_compat import OpenAICompatAuth

# Order matters for the ``auth.list`` UI: subscription / API key first
# so the wizard's preferred Anthropic options are at the top.
_DEFAULT_KIND_ORDER: tuple[AuthBackendKind, ...] = (
    AuthBackendKind.CLAUDE_SUBSCRIPTION,
    AuthBackendKind.ANTHROPIC_API,
    AuthBackendKind.OPENAI_COMPAT,
    AuthBackendKind.OLLAMA,
    AuthBackendKind.LLAMA_CPP,
    AuthBackendKind.MLX,
)


_DESCRIPTIONS: Mapping[AuthBackendKind, str] = {
    AuthBackendKind.CLAUDE_SUBSCRIPTION: "Use your Claude subscription via the bundled CLI.",
    AuthBackendKind.ANTHROPIC_API: "Paste an Anthropic API key.",
    AuthBackendKind.OPENAI_COMPAT: "Bring your own OpenAI-compatible endpoint and key.",
    AuthBackendKind.OLLAMA: "Run a local Ollama server.",
    AuthBackendKind.LLAMA_CPP: "Run a local llama.cpp HTTP server.",
    AuthBackendKind.MLX: "Run on-device MLX inference (Apple Silicon).",
}


_DISPLAY_NAMES: Mapping[AuthBackendKind, str] = {
    AuthBackendKind.CLAUDE_SUBSCRIPTION: "Claude subscription",
    AuthBackendKind.ANTHROPIC_API: "Anthropic API key",
    AuthBackendKind.OPENAI_COMPAT: "OpenAI-compatible endpoint",
    AuthBackendKind.OLLAMA: "Ollama (local)",
    AuthBackendKind.LLAMA_CPP: "llama.cpp (local)",
    AuthBackendKind.MLX: "MLX (Apple Silicon)",
}


def _default_factories() -> Mapping[AuthBackendKind, Any]:
    """Return zero-argument factories for each kind's default instance.

    Each factory is invoked lazily so probe-time imports (httpx,
    platform, the SDK) only happen when the kind is actually used.
    """
    return {
        AuthBackendKind.CLAUDE_SUBSCRIPTION: ClaudeSubscriptionAuth,
        AuthBackendKind.ANTHROPIC_API: AnthropicApiAuth,
        AuthBackendKind.OPENAI_COMPAT: OpenAICompatAuth,
        AuthBackendKind.OLLAMA: OllamaAuth,
        AuthBackendKind.LLAMA_CPP: LlamaCppAuth,
        AuthBackendKind.MLX: MlxAuth,
    }


class AuthBackendRegistry:
    """Holds the active ``AuthBackend`` per kind + a global active kind.

    Instances are constructed lazily on first request so the
    Anthropic-only path doesn't pay the import cost for every backend.
    The registry is not thread-safe — it lives on the asyncio event
    loop alongside the dispatcher.
    """

    def __init__(
        self,
        *,
        active_kind: AuthBackendKind = AuthBackendKind.CLAUDE_SUBSCRIPTION,
        factories: Mapping[AuthBackendKind, Any] | None = None,
    ) -> None:
        self._factories = dict(factories) if factories is not None else dict(_default_factories())
        self._instances: dict[AuthBackendKind, AuthBackend] = {}
        if active_kind not in self._factories:
            raise ValueError(f"no factory registered for kind: {active_kind}")
        self._active_kind = active_kind

    def list_kinds(self) -> list[AuthBackendKind]:
        """Stable, UI-friendly ordering."""
        return [kind for kind in _DEFAULT_KIND_ORDER if kind in self._factories]

    @property
    def active_kind(self) -> AuthBackendKind:
        return self._active_kind

    def set_active(self, kind: AuthBackendKind) -> None:
        if kind not in self._factories:
            raise AuthBackendError(f"no factory registered for kind: {kind}")
        self._active_kind = kind

    def instance(self, kind: AuthBackendKind) -> AuthBackend:
        existing = self._instances.get(kind)
        if existing is not None:
            return existing
        factory = self._factories.get(kind)
        if factory is None:
            raise AuthBackendError(f"no factory registered for kind: {kind}")
        instance: AuthBackend = factory()
        self._instances[kind] = instance
        return instance

    def active(self) -> AuthBackend:
        return self.instance(self._active_kind)

    def descriptor(self, kind: AuthBackendKind) -> dict[str, Any]:
        """Wire-friendly metadata for ``auth.list``."""
        return {
            "kind": kind.value,
            "displayName": _DISPLAY_NAMES[kind],
            "description": _DESCRIPTIONS[kind],
            "active": kind == self._active_kind,
        }

    async def probe(self, kind: AuthBackendKind) -> AuthProbeResult:
        instance = self.instance(kind)
        return await instance.probe()
