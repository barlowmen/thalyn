"""Tests for the OpenAI-compatible auth-backend adapter."""

from __future__ import annotations

import pytest
from thalyn_brain.provider.auth import (
    AuthBackend,
    AuthBackendKind,
    AuthBackendNotAuthenticatedError,
)
from thalyn_brain.provider.auth_openai_compat import OpenAICompatAuth


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


def test_openai_compat_satisfies_protocol() -> None:
    backend = OpenAICompatAuth()
    assert isinstance(backend, AuthBackend)
    assert backend.kind == AuthBackendKind.OPENAI_COMPAT


def test_openai_compat_default_base_url() -> None:
    assert OpenAICompatAuth().base_url == "https://api.openai.com/v1"


def test_openai_compat_strips_trailing_slash_from_base_url() -> None:
    backend = OpenAICompatAuth(base_url="https://example.com/v1/")
    assert backend.base_url == "https://example.com/v1"


def test_openai_compat_default_env_var_is_openai_api_key() -> None:
    assert OpenAICompatAuth().env_var == "OPENAI_API_KEY"


async def test_openai_compat_reads_default_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
    backend = OpenAICompatAuth()
    assert await backend.token() == "sk-from-env"
    result = await backend.probe()
    assert result.authenticated is True


async def test_openai_compat_no_key_reports_unauthenticated() -> None:
    backend = OpenAICompatAuth()
    result = await backend.probe()
    assert result.detected is True
    assert result.authenticated is False
    assert "OPENAI_API_KEY" in (result.detail or "")
    with pytest.raises(AuthBackendNotAuthenticatedError):
        await backend.ensure_ready()


async def test_openai_compat_accepts_literal_string_source() -> None:
    backend = OpenAICompatAuth(source="sk-literal", base_url="https://groq.example/v1")
    assert await backend.token() == "sk-literal"
    result = await backend.probe()
    assert result.authenticated is True
    assert "groq.example" in (result.detail or "")


async def test_openai_compat_accepts_async_callable_source() -> None:
    async def fetch() -> str:
        return "sk-async"

    backend = OpenAICompatAuth(source=fetch)
    assert await backend.token() == "sk-async"


async def test_openai_compat_accepts_sync_callable_source() -> None:
    def fetch() -> str:
        return "sk-sync"

    backend = OpenAICompatAuth(source=fetch)
    assert await backend.token() == "sk-sync"


async def test_openai_compat_custom_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CUSTOM_LLM_KEY", "sk-custom")
    backend = OpenAICompatAuth(env_var="CUSTOM_LLM_KEY")
    assert await backend.token() == "sk-custom"
