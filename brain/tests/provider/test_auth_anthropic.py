"""Tests for the Anthropic auth-backend adapters."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import stat
import textwrap
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from thalyn_brain.provider.auth import (
    AuthBackend,
    AuthBackendKind,
    AuthBackendNotAuthenticatedError,
    AuthBackendNotDetectedError,
)
from thalyn_brain.provider.auth_anthropic import (
    AnthropicApiAuth,
    ClaudeSubscriptionAuth,
    find_claude_cli,
)

# ---------------------------------------------------------------------------
# Helpers — fake claude binaries written into tmp_path
# ---------------------------------------------------------------------------


def _write_fake_cli(
    tmp_path: Path,
    *,
    stdout: str = "",
    stderr: str = "",
    returncode: int = 0,
    sleep_ms: int = 0,
) -> Path:
    """Write a tiny ``claude``-shaped Python shim into ``tmp_path`` and
    return its path. The shim accepts any args, sleeps for
    ``sleep_ms`` ms, prints ``stdout`` / ``stderr``, and exits
    ``returncode``."""
    script = tmp_path / "claude"
    script.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env python3
            import sys, time
            time.sleep({sleep_ms / 1000})
            if {stdout!r}:
                sys.stdout.write({stdout!r})
            if {stderr!r}:
                sys.stderr.write({stderr!r})
            sys.exit({returncode})
            """
        )
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return script


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    yield


# ---------------------------------------------------------------------------
# find_claude_cli
# ---------------------------------------------------------------------------


def test_find_claude_cli_returns_a_path_or_none() -> None:
    # Smoke: just ensure the lookup doesn't crash and returns the right
    # type. Whether a binary exists depends on the developer's machine.
    result = find_claude_cli()
    assert result is None or isinstance(result, str)
    if result is not None:
        assert Path(result).exists()


# ---------------------------------------------------------------------------
# ClaudeSubscriptionAuth
# ---------------------------------------------------------------------------


def test_subscription_auth_satisfies_protocol() -> None:
    backend = ClaudeSubscriptionAuth()
    assert isinstance(backend, AuthBackend)
    assert backend.kind == AuthBackendKind.CLAUDE_SUBSCRIPTION


async def test_subscription_probe_reports_not_detected_when_cli_missing() -> None:
    backend = ClaudeSubscriptionAuth(cli_locator=lambda: None)
    result = await backend.probe()
    assert result.detected is False
    assert result.authenticated is False
    assert "not found" in (result.detail or "").lower()


async def test_subscription_probe_reports_authenticated_on_logged_in_cli(
    tmp_path: Path,
) -> None:
    payload = json.dumps(
        {"loggedIn": True, "authMethod": "oauth_token", "apiProvider": "firstParty"}
    )
    cli = _write_fake_cli(tmp_path, stdout=payload)
    backend = ClaudeSubscriptionAuth(cli_locator=lambda: str(cli))

    result = await backend.probe()
    assert result.detected is True
    assert result.authenticated is True
    assert "Claude subscription" in (result.detail or "")
    assert "oauth_token" in (result.detail or "")
    # Caching: a second call returns the same instance without re-running.
    assert (await backend.probe()) is result
    backend.invalidate_probe_cache()
    # After invalidation we re-run the probe — same outcome because the
    # fake cli is deterministic, but a fresh result instance.
    refreshed = await backend.probe()
    assert refreshed.authenticated is True


async def test_subscription_probe_reports_unauthenticated_on_logged_out_cli(
    tmp_path: Path,
) -> None:
    payload = json.dumps({"loggedIn": False})
    cli = _write_fake_cli(tmp_path, stdout=payload)
    backend = ClaudeSubscriptionAuth(cli_locator=lambda: str(cli))

    result = await backend.probe()
    assert result.detected is True
    assert result.authenticated is False
    assert "not logged in" in (result.detail or "").lower()


async def test_subscription_probe_surfaces_non_zero_exit(tmp_path: Path) -> None:
    cli = _write_fake_cli(
        tmp_path,
        stderr="auth subsystem unavailable",
        returncode=2,
    )
    backend = ClaudeSubscriptionAuth(cli_locator=lambda: str(cli))

    result = await backend.probe()
    assert result.detected is True
    assert result.authenticated is False
    assert result.error is not None
    assert "auth subsystem" in result.error


async def test_subscription_probe_surfaces_unparseable_json(tmp_path: Path) -> None:
    cli = _write_fake_cli(tmp_path, stdout="not json{}")
    backend = ClaudeSubscriptionAuth(cli_locator=lambda: str(cli))

    result = await backend.probe()
    assert result.detected is True
    assert result.authenticated is False
    assert result.error is not None
    assert "JSON" in result.error or "json" in result.error


@pytest.mark.skipif(shutil.which("python3") is None, reason="python3 unavailable")
async def test_subscription_probe_kills_a_hung_cli(tmp_path: Path) -> None:
    # Shim sleeps longer than the probe timeout. Patch the constant so the
    # test doesn't actually wait 3 seconds.
    cli = _write_fake_cli(tmp_path, stdout="ignored", sleep_ms=2000)

    import thalyn_brain.provider.auth_anthropic as mod

    old_timeout = mod._PROBE_TIMEOUT_SECS
    mod._PROBE_TIMEOUT_SECS = 0.2
    try:
        backend = ClaudeSubscriptionAuth(cli_locator=lambda: str(cli))
        result = await backend.probe()
    finally:
        mod._PROBE_TIMEOUT_SECS = old_timeout

    assert result.authenticated is False
    assert result.error is not None
    assert "timed out" in result.error


async def test_subscription_token_is_none() -> None:
    backend = ClaudeSubscriptionAuth(cli_locator=lambda: None)
    assert await backend.token() is None


async def test_subscription_ensure_ready_raises_when_cli_missing() -> None:
    backend = ClaudeSubscriptionAuth(cli_locator=lambda: None)
    with pytest.raises(AuthBackendNotDetectedError):
        await backend.ensure_ready()


async def test_subscription_ensure_ready_raises_when_logged_out(tmp_path: Path) -> None:
    cli = _write_fake_cli(tmp_path, stdout=json.dumps({"loggedIn": False}))
    backend = ClaudeSubscriptionAuth(cli_locator=lambda: str(cli))
    with pytest.raises(AuthBackendNotAuthenticatedError):
        await backend.ensure_ready()


async def test_subscription_ensure_ready_passes_when_logged_in(tmp_path: Path) -> None:
    cli = _write_fake_cli(
        tmp_path,
        stdout=json.dumps({"loggedIn": True, "authMethod": "oauth_token"}),
    )
    backend = ClaudeSubscriptionAuth(cli_locator=lambda: str(cli))
    await backend.ensure_ready()  # no exception


# ---------------------------------------------------------------------------
# AnthropicApiAuth
# ---------------------------------------------------------------------------


def test_api_auth_satisfies_protocol() -> None:
    backend = AnthropicApiAuth()
    assert isinstance(backend, AuthBackend)
    assert backend.kind == AuthBackendKind.ANTHROPIC_API


async def test_api_auth_default_reads_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-from-env")
    backend = AnthropicApiAuth()
    assert await backend.token() == "sk-from-env"
    result = await backend.probe()
    assert result.detected is True
    assert result.authenticated is True


async def test_api_auth_no_env_var_reports_unauthenticated() -> None:
    backend = AnthropicApiAuth()
    result = await backend.probe()
    assert result.detected is True
    assert result.authenticated is False
    assert "no ANTHROPIC_API_KEY" in (result.detail or "")
    with pytest.raises(AuthBackendNotAuthenticatedError):
        await backend.ensure_ready()


async def test_api_auth_accepts_literal_string_source() -> None:
    backend = AnthropicApiAuth(source="sk-explicit")
    assert await backend.token() == "sk-explicit"
    result = await backend.probe()
    assert result.authenticated is True


async def test_api_auth_accepts_sync_callable_source() -> None:
    calls: list[int] = []

    def fetch() -> str:
        calls.append(1)
        return "sk-fetched"

    backend = AnthropicApiAuth(source=fetch)
    assert await backend.token() == "sk-fetched"
    assert await backend.token() == "sk-fetched"
    # Resolved on every call so a hot-rotated key becomes visible.
    assert len(calls) == 2


async def test_api_auth_accepts_async_callable_source() -> None:
    async def fetch() -> str:
        await asyncio.sleep(0)
        return "sk-async"

    backend = AnthropicApiAuth(source=fetch)
    assert await backend.token() == "sk-async"


async def test_api_auth_treats_empty_string_as_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    backend = AnthropicApiAuth()
    result = await backend.probe()
    assert result.authenticated is False


def test_api_auth_env_var_constant_matches_documented_contract() -> None:
    # Hard contract: the rest of the brain reads this same env name.
    assert AnthropicApiAuth._ENV_VAR == "ANTHROPIC_API_KEY"
    # Preserve the contract that the v1 spawn-env path keeps working.
    assert os.environ.get("ANTHROPIC_API_KEY") in (None, "")


def test_api_auth_does_not_leak_module_state_between_instances() -> None:
    a = AnthropicApiAuth(source="key-a")
    b = AnthropicApiAuth(source="key-b")
    # Sources are instance-scoped; one backend's source should not
    # accidentally bleed into another via shared mutable state.
    assert a is not b
    assert a._source != b._source  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "kind, expected_value",
    [
        (AuthBackendKind.CLAUDE_SUBSCRIPTION, "claude_subscription"),
        (AuthBackendKind.ANTHROPIC_API, "anthropic_api"),
    ],
)
def test_kind_values_match_storage_contract(kind: AuthBackendKind, expected_value: str) -> None:
    # Persisted values in the auth_backends.kind column must round-trip
    # through the runtime enum.
    assert kind.value == expected_value


def test_unused_import_anchor() -> None:
    # Keeps the ``Any`` import used so ruff doesn't trim it from the
    # test module's typing imports if someone refactors later.
    assert Any is Any
