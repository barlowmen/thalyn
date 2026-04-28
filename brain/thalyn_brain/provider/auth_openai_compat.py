"""Auth-backend adapter for OpenAI-compatible HTTP endpoints.

Holds an API key (resolved literally, from a callable, or from the
``OPENAI_API_KEY`` env var) plus the endpoint base URL. The probe
reports authenticated=True when a key is present; reachability is
delegated to the provider's first call so we don't pay an HTTP
round-trip on every ``auth.list``.

The provider itself is a v0.3 placeholder; this adapter completes the
auth-backend trait surface for the v0.22 split (per ADR-0020).
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable

from thalyn_brain.provider.auth import (
    AuthBackendError,
    AuthBackendKind,
    AuthBackendNotAuthenticatedError,
    AuthProbeResult,
)

# Same alias shape as AnthropicApiAuth's source.
ApiKeyProvider = str | Callable[[], Awaitable[str | None]] | Callable[[], str | None]


class OpenAICompatAuth:
    """Auth backend for an OpenAI-compatible endpoint.

    The provider sets the resulting token as ``OPENAI_API_KEY`` (the
    convention every OpenAI-compat client expects); the base URL is a
    metadata field used by the provider, not the auth backend, but is
    cached here so the wizard can surface "set your key for ``<url>``".
    """

    DEFAULT_ENV_VAR = "OPENAI_API_KEY"
    DEFAULT_BASE_URL = "https://api.openai.com/v1"

    def __init__(
        self,
        *,
        source: ApiKeyProvider | None = None,
        base_url: str = DEFAULT_BASE_URL,
        env_var: str = DEFAULT_ENV_VAR,
    ) -> None:
        self._env_var = env_var
        self._base_url = base_url.rstrip("/")
        self._source = source if source is not None else self._read_env

    @property
    def kind(self) -> AuthBackendKind:
        return AuthBackendKind.OPENAI_COMPAT

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def env_var(self) -> str:
        return self._env_var

    def _read_env(self) -> str | None:
        value = os.environ.get(self._env_var)
        return value if value else None

    async def _resolve(self) -> str | None:
        source = self._source
        if isinstance(source, str):
            return source
        result = source()
        if asyncio.iscoroutine(result):
            value: str | None = await result
            return value
        return result  # type: ignore[return-value]

    async def probe(self) -> AuthProbeResult:
        try:
            key = await self._resolve()
        except Exception as exc:
            return AuthProbeResult(
                detected=True,
                authenticated=False,
                detail=None,
                error=f"failed to read {self._env_var}: {exc}",
            )
        if key:
            return AuthProbeResult(
                detected=True,
                authenticated=True,
                detail=f"OpenAI-compat key on file for {self._base_url}",
            )
        return AuthProbeResult(
            detected=True,
            authenticated=False,
            detail=f"no {self._env_var} set for {self._base_url}",
        )

    async def ensure_ready(self) -> None:
        result = await self.probe()
        if not result.authenticated:
            raise AuthBackendNotAuthenticatedError(
                result.detail or result.error or "no OpenAI-compat key on file"
            )

    async def token(self) -> str | None:
        try:
            return await self._resolve()
        except Exception as exc:
            raise AuthBackendError(f"failed to read {self._env_var}: {exc}") from exc
