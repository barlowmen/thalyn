"""Auth-backend adapters for the local-runtime providers.

Three adapters that follow the same shape: ``token()`` always
returns ``None`` (local runtimes don't carry a credential), the
probe reports whether the runtime substrate is reachable, and
``ensure_ready`` surfaces a clean error when it isn't.

- ``OllamaAuth`` — pings the Ollama HTTP endpoint (default
  ``http://localhost:11434``). The endpoint is reachable iff Ollama
  is running.
- ``LlamaCppAuth`` — placeholder for a future llama.cpp server
  endpoint; v0.3 marks the provider not-implemented at runtime, and
  the auth probe matches that surface so the wizard can explain why.
- ``MlxAuth`` — Apple Silicon only; probes the platform and reports
  detected=False everywhere else so the wizard can offer the right
  alternative.

These adapters complete the v0.22 trait surface (per ADR-0020). The
underlying providers stay placeholders until later phases light them
up; the auth surface is wired now so the first-run flow can branch
on probe state regardless of provider readiness.
"""

from __future__ import annotations

import asyncio
import platform
from typing import Any

import httpx

from thalyn_brain.provider.auth import (
    AuthBackendKind,
    AuthBackendNotAuthenticatedError,
    AuthBackendNotDetectedError,
    AuthProbeResult,
)

# Conservative timeout for the HTTP probe; > 2 s means the user's
# loopback is broken or the runtime is stuck behind blocking I/O.
_HTTP_PROBE_TIMEOUT_SECS = 2.0


class OllamaAuth:
    """Auth backend for the local Ollama runtime. Reachability check
    only — Ollama has no per-user credentials."""

    DEFAULT_BASE_URL = "http://localhost:11434"

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        http_client_factory: Any = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        # Indirection so tests can swap in a mocked client without
        # patching httpx globally; signature mirrors httpx.AsyncClient.
        self._http_client_factory = http_client_factory

    @property
    def kind(self) -> AuthBackendKind:
        return AuthBackendKind.OLLAMA

    @property
    def base_url(self) -> str:
        return self._base_url

    async def probe(self) -> AuthProbeResult:
        url = f"{self._base_url}/api/tags"
        factory = self._http_client_factory or httpx.AsyncClient
        try:
            async with asyncio.timeout(_HTTP_PROBE_TIMEOUT_SECS):
                async with factory(timeout=_HTTP_PROBE_TIMEOUT_SECS) as client:
                    response = await client.get(url)
        except TimeoutError:
            return AuthProbeResult(
                detected=False,
                authenticated=False,
                detail=None,
                error=f"Ollama probe timed out after {_HTTP_PROBE_TIMEOUT_SECS:.0f}s",
            )
        except httpx.HTTPError as exc:
            return AuthProbeResult(
                detected=False,
                authenticated=False,
                detail=None,
                error=f"Ollama unreachable at {self._base_url}: {exc}",
            )

        if response.status_code != 200:
            return AuthProbeResult(
                detected=True,
                authenticated=False,
                detail=None,
                error=f"Ollama replied with HTTP {response.status_code}",
            )
        return AuthProbeResult(
            detected=True,
            authenticated=True,
            detail=f"Ollama running at {self._base_url}",
        )

    async def ensure_ready(self) -> None:
        result = await self.probe()
        if not result.detected:
            raise AuthBackendNotDetectedError(
                result.error or result.detail or "Ollama not reachable"
            )
        if not result.authenticated:
            raise AuthBackendNotAuthenticatedError(
                result.error or result.detail or "Ollama responded with an error"
            )

    async def token(self) -> str | None:
        return None


class LlamaCppAuth:
    """Auth backend for a llama.cpp HTTP server. v0.3 placeholder —
    the provider is not yet implemented and the auth probe surfaces
    the same not-detected state so the UI can branch on it."""

    DEFAULT_BASE_URL = "http://localhost:8080"

    def __init__(self, *, base_url: str = DEFAULT_BASE_URL) -> None:
        self._base_url = base_url.rstrip("/")

    @property
    def kind(self) -> AuthBackendKind:
        return AuthBackendKind.LLAMA_CPP

    @property
    def base_url(self) -> str:
        return self._base_url

    async def probe(self) -> AuthProbeResult:
        # The llama.cpp server's /health endpoint returns 200 when the
        # model is loaded. We probe it the same way Ollama's probe
        # does; a connection error is the not-detected signal.
        url = f"{self._base_url}/health"
        try:
            async with asyncio.timeout(_HTTP_PROBE_TIMEOUT_SECS):
                async with httpx.AsyncClient(timeout=_HTTP_PROBE_TIMEOUT_SECS) as client:
                    response = await client.get(url)
        except TimeoutError:
            return AuthProbeResult(
                detected=False,
                authenticated=False,
                detail=None,
                error=f"llama.cpp probe timed out after {_HTTP_PROBE_TIMEOUT_SECS:.0f}s",
            )
        except httpx.HTTPError:
            return AuthProbeResult(
                detected=False,
                authenticated=False,
                detail=f"llama.cpp server not running at {self._base_url}",
            )

        authenticated = response.status_code == 200
        return AuthProbeResult(
            detected=True,
            authenticated=authenticated,
            detail=(
                f"llama.cpp server at {self._base_url}"
                if authenticated
                else f"llama.cpp replied with HTTP {response.status_code}"
            ),
        )

    async def ensure_ready(self) -> None:
        result = await self.probe()
        if not result.authenticated:
            raise AuthBackendNotDetectedError(
                result.error or result.detail or "llama.cpp not reachable"
            )

    async def token(self) -> str | None:
        return None


class MlxAuth:
    """Auth backend for the on-device MLX runtime. Apple Silicon only.

    The probe checks the platform; on non-Apple-Silicon hosts it
    reports detected=False so the wizard can offer the right
    alternative without spawning the MLX provider only to fail at
    import time."""

    def __init__(self, *, system_probe: Any = None) -> None:
        # ``system_probe`` is a callable ``() -> tuple[str, str]``
        # returning ``(system, machine)``; defaults to platform.* for
        # production. Tests pass a stub to simulate other hosts.
        self._system_probe = system_probe or _default_platform_probe

    @property
    def kind(self) -> AuthBackendKind:
        return AuthBackendKind.MLX

    async def probe(self) -> AuthProbeResult:
        system, machine = self._system_probe()
        if system != "Darwin":
            return AuthProbeResult(
                detected=False,
                authenticated=False,
                detail=f"MLX requires macOS; detected {system or 'unknown'}",
            )
        if machine != "arm64":
            return AuthProbeResult(
                detected=False,
                authenticated=False,
                detail=f"MLX requires Apple Silicon; detected {machine or 'unknown'}",
            )
        # On Apple Silicon, mlx_lm is loaded lazily by the provider
        # itself; the auth probe doesn't import it (so the brain
        # doesn't pay the model-load cost just to enumerate auth
        # backends).
        return AuthProbeResult(
            detected=True,
            authenticated=True,
            detail="Apple Silicon detected; MLX runtime available",
        )

    async def ensure_ready(self) -> None:
        result = await self.probe()
        if not result.detected:
            raise AuthBackendNotDetectedError(
                result.detail or "MLX runtime not available on this host"
            )

    async def token(self) -> str | None:
        return None


def _default_platform_probe() -> tuple[str, str]:
    return platform.system(), platform.machine()
