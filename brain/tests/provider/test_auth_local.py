"""Tests for the local-runtime auth-backend adapters."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from thalyn_brain.provider.auth import (
    AuthBackend,
    AuthBackendKind,
    AuthBackendNotAuthenticatedError,
    AuthBackendNotDetectedError,
)
from thalyn_brain.provider.auth_local import (
    LlamaCppAuth,
    MlxAuth,
    OllamaAuth,
)

# ---------------------------------------------------------------------------
# OllamaAuth
# ---------------------------------------------------------------------------


class _MockHttpClient:
    """Tiny async-context-manager that returns a scripted response."""

    def __init__(self, *, status_code: int = 200, raise_exc: BaseException | None = None) -> None:
        self._status_code = status_code
        self._raise_exc = raise_exc
        self.requested_urls: list[str] = []

    async def __aenter__(self) -> _MockHttpClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def get(self, url: str) -> Any:
        self.requested_urls.append(url)
        if self._raise_exc is not None:
            raise self._raise_exc
        return _Response(self._status_code)


class _Response:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


def _client_factory(client: _MockHttpClient) -> Any:
    def factory(**kwargs: Any) -> _MockHttpClient:
        return client

    return factory


def test_ollama_auth_satisfies_protocol() -> None:
    backend = OllamaAuth()
    assert isinstance(backend, AuthBackend)
    assert backend.kind == AuthBackendKind.OLLAMA


def test_ollama_default_base_url_is_loopback() -> None:
    assert OllamaAuth().base_url == "http://localhost:11434"


def test_ollama_strips_trailing_slash_from_base_url() -> None:
    backend = OllamaAuth(base_url="http://localhost:11434/")
    assert backend.base_url == "http://localhost:11434"


async def test_ollama_probe_reports_running_endpoint() -> None:
    client = _MockHttpClient(status_code=200)
    backend = OllamaAuth(http_client_factory=_client_factory(client))

    result = await backend.probe()

    assert result.detected is True
    assert result.authenticated is True
    assert "Ollama running" in (result.detail or "")
    assert client.requested_urls == ["http://localhost:11434/api/tags"]


async def test_ollama_probe_reports_unreachable_on_connection_error() -> None:
    client = _MockHttpClient(raise_exc=httpx.ConnectError("conn refused"))
    backend = OllamaAuth(http_client_factory=_client_factory(client))

    result = await backend.probe()

    assert result.detected is False
    assert result.authenticated is False
    assert result.error is not None
    assert "unreachable" in result.error


async def test_ollama_probe_reports_unauthenticated_on_non_200() -> None:
    client = _MockHttpClient(status_code=503)
    backend = OllamaAuth(http_client_factory=_client_factory(client))

    result = await backend.probe()

    assert result.detected is True
    assert result.authenticated is False
    assert result.error is not None
    assert "503" in result.error


async def test_ollama_token_is_none() -> None:
    backend = OllamaAuth(http_client_factory=_client_factory(_MockHttpClient()))
    assert await backend.token() is None


async def test_ollama_ensure_ready_raises_on_unreachable() -> None:
    client = _MockHttpClient(raise_exc=httpx.ConnectError("nope"))
    backend = OllamaAuth(http_client_factory=_client_factory(client))
    with pytest.raises(AuthBackendNotDetectedError):
        await backend.ensure_ready()


async def test_ollama_ensure_ready_raises_on_non_200() -> None:
    client = _MockHttpClient(status_code=503)
    backend = OllamaAuth(http_client_factory=_client_factory(client))
    with pytest.raises(AuthBackendNotAuthenticatedError):
        await backend.ensure_ready()


# ---------------------------------------------------------------------------
# LlamaCppAuth
# ---------------------------------------------------------------------------


def test_llama_cpp_auth_satisfies_protocol() -> None:
    backend = LlamaCppAuth()
    assert isinstance(backend, AuthBackend)
    assert backend.kind == AuthBackendKind.LLAMA_CPP


def test_llama_cpp_default_base_url() -> None:
    assert LlamaCppAuth().base_url == "http://localhost:8080"


async def test_llama_cpp_token_is_none() -> None:
    backend = LlamaCppAuth()
    assert await backend.token() is None


# ---------------------------------------------------------------------------
# MlxAuth
# ---------------------------------------------------------------------------


@pytest.fixture
def _apple_silicon() -> Iterator[Any]:
    yield lambda: ("Darwin", "arm64")


@pytest.fixture
def _intel_mac() -> Iterator[Any]:
    yield lambda: ("Darwin", "x86_64")


@pytest.fixture
def _linux_x64() -> Iterator[Any]:
    yield lambda: ("Linux", "x86_64")


def test_mlx_auth_satisfies_protocol() -> None:
    backend = MlxAuth()
    assert isinstance(backend, AuthBackend)
    assert backend.kind == AuthBackendKind.MLX


async def test_mlx_probe_reports_ready_on_apple_silicon(_apple_silicon: Any) -> None:
    backend = MlxAuth(system_probe=_apple_silicon)
    result = await backend.probe()
    assert result.detected is True
    assert result.authenticated is True
    assert "Apple Silicon" in (result.detail or "")


async def test_mlx_probe_reports_not_detected_on_intel_mac(_intel_mac: Any) -> None:
    backend = MlxAuth(system_probe=_intel_mac)
    result = await backend.probe()
    assert result.detected is False
    assert "Apple Silicon" in (result.detail or "")
    assert "x86_64" in (result.detail or "")


async def test_mlx_probe_reports_not_detected_on_linux(_linux_x64: Any) -> None:
    backend = MlxAuth(system_probe=_linux_x64)
    result = await backend.probe()
    assert result.detected is False
    assert "macOS" in (result.detail or "")


async def test_mlx_token_is_none(_apple_silicon: Any) -> None:
    backend = MlxAuth(system_probe=_apple_silicon)
    assert await backend.token() is None


async def test_mlx_ensure_ready_raises_off_apple_silicon(_linux_x64: Any) -> None:
    backend = MlxAuth(system_probe=_linux_x64)
    with pytest.raises(AuthBackendNotDetectedError):
        await backend.ensure_ready()


async def test_mlx_ensure_ready_passes_on_apple_silicon(_apple_silicon: Any) -> None:
    backend = MlxAuth(system_probe=_apple_silicon)
    await backend.ensure_ready()  # no exception
