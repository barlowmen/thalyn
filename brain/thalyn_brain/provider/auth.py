"""Auth-backend Protocol — splits *how to authenticate* from *which model
to call*.

Mirrors the Rust trait in ``src-tauri/src/provider/auth.rs``. Concrete
adapters live in ``thalyn_brain/provider/auth_*.py``; the
``AnthropicProvider`` composes one and delegates the env / token
question to it before talking to the SDK.

The four states a probe can land in:

- *not detected* — the auth source isn't installed (CLI not on PATH,
  endpoint not reachable). The UI can offer install help.
- *detected, not authenticated* — installed but no credential present
  (CLI not logged in, no API key on file). The UI can offer a login
  step.
- *detected, authenticated* — the happy path. The provider is ready.
- *probe error* — something went wrong checking. Treat as not
  authenticated and surface ``error`` to the user.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable


class AuthBackendKind(StrEnum):
    """Identifier for the credential source.

    These values are the wire / storage form. They match the ``kind``
    column of the ``auth_backends`` SQLite table (migration 003) and
    the Rust enum's snake_case serde rendering.
    """

    CLAUDE_SUBSCRIPTION = "claude_subscription"
    ANTHROPIC_API = "anthropic_api"
    OPENAI_COMPAT = "openai_compat"
    OLLAMA = "ollama"
    LLAMA_CPP = "llama_cpp"
    MLX = "mlx"


@dataclass(frozen=True)
class AuthProbeResult:
    """Result of asking an auth backend "are you ready right now?"."""

    detected: bool
    """The backend's substrate is reachable (CLI on PATH, endpoint
    responding, etc.)."""

    authenticated: bool
    """The backend has a usable credential (logged in, key on file)."""

    detail: str | None = None
    """Short human-readable status, e.g. ``"Claude subscription
    (oauth_token)"`` or ``"API key on file"``. Surfaced in the UI."""

    error: str | None = None
    """Populated when the probe itself failed (subprocess died, JSON
    parse error). Mutually informative with ``detected``: a probe
    that errors is reported as not detected."""

    def to_wire(self) -> dict[str, Any]:
        return {
            "detected": self.detected,
            "authenticated": self.authenticated,
            "detail": self.detail,
            "error": self.error,
        }


@runtime_checkable
class AuthBackend(Protocol):
    """Runtime protocol for an auth backend.

    Decoupled from ``LlmProvider`` so a single provider class can be
    composed with either subscription or API-key auth (the v0.22 split
    per ``02-architecture.md`` §7).
    """

    @property
    def kind(self) -> AuthBackendKind: ...

    async def probe(self) -> AuthProbeResult:
        """Cheap reachability + auth check. Cacheable per the adapter's
        own freshness rules; callers should not assume idempotence
        within long sessions."""
        ...

    async def ensure_ready(self) -> None:
        """Raise ``AuthBackendError`` if the backend can't be used right
        now. Adapters that need a one-shot login flow drive it from
        here; the default implementation is a probe + raise."""
        ...

    async def token(self) -> str | None:
        """Return the credential to inject into the provider's call
        environment, or ``None`` when the backend manages auth out of
        band (e.g. ``ClaudeSubscriptionAuth``, where the bundled CLI
        owns the OAuth token).

        For Anthropic-family providers a non-``None`` return is set as
        ``ANTHROPIC_API_KEY`` in the SDK's spawn env; ``None`` lets the
        SDK / CLI use whatever auth state it already holds."""
        ...


class AuthBackendError(Exception):
    """Surfaced when an auth backend cannot satisfy a request."""


class AuthBackendNotDetectedError(AuthBackendError):
    """The backend's substrate isn't installed or reachable."""


class AuthBackendNotAuthenticatedError(AuthBackendError):
    """The backend is installed but has no usable credential."""
