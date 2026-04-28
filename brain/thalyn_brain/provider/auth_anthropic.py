"""Auth-backend adapters for the Anthropic family.

Two adapters share the same model surface (``AnthropicProvider``) and
differ only in what they put in the SDK's spawn env:

- ``ClaudeSubscriptionAuth`` — probes ``claude auth status --json`` and
  returns ``token() = None`` so the bundled CLI's stored OAuth token
  flows through. The default brain auth per ADR-0020.
- ``AnthropicApiAuth`` — reads ``ANTHROPIC_API_KEY`` from the brain's
  spawn env (Rust core injects it from the OS keychain) and returns
  the key from ``token()`` so the provider can set it explicitly on
  the SDK call.

The probe shape, ``claude`` CLI lookup order, and the
``no API_KEY → subscription path`` invariant are recorded in
``docs/spikes/2026-04-28-claude-cli-auth.md``.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path

from thalyn_brain.provider.auth import (
    AuthBackendError,
    AuthBackendKind,
    AuthBackendNotAuthenticatedError,
    AuthBackendNotDetectedError,
    AuthProbeResult,
)

# Conservative timeout for the CLI probe. ``claude auth status`` is a
# local read; > 3 s means the binary hung or is stuck behind a
# blocking dialog and the user shouldn't wait on it.
_PROBE_TIMEOUT_SECS = 3.0

# Locations the bundled SDK + popular installers drop the CLI. The
# Claude Agent SDK's own ``_find_cli`` scans the bundled binary first;
# we mirror that order so the same binary runs both the probe and the
# subsequent SDK calls.
_FALLBACK_CLI_LOCATIONS: tuple[str, ...] = (
    str(Path.home() / ".npm-global/bin/claude"),
    "/usr/local/bin/claude",
    str(Path.home() / ".local/bin/claude"),
    str(Path.home() / "node_modules/.bin/claude"),
    str(Path.home() / ".yarn/bin/claude"),
    str(Path.home() / ".claude/local/claude"),
)


def _bundled_cli_path() -> str | None:
    """Return the SDK's bundled ``claude`` binary path if present."""
    try:
        import claude_agent_sdk
    except ImportError:
        return None
    bundled = Path(claude_agent_sdk.__file__).parent / "_bundled" / "claude"
    return str(bundled) if bundled.is_file() else None


def find_claude_cli() -> str | None:
    """Locate the ``claude`` CLI binary, preferring the SDK's bundled
    copy so the probe and subsequent SDK calls run the same binary."""
    if path := _bundled_cli_path():
        return path
    if path := shutil.which("claude"):
        return path
    for candidate in _FALLBACK_CLI_LOCATIONS:
        if Path(candidate).is_file():
            return candidate
    return None


class ClaudeSubscriptionAuth:
    """Auth backend that delegates to the ``claude`` CLI's stored
    OAuth token. The default brain auth per ADR-0020."""

    def __init__(self, *, cli_locator: Callable[[], str | None] | None = None) -> None:
        self._locator = cli_locator or find_claude_cli
        self._cached: AuthProbeResult | None = None

    @property
    def kind(self) -> AuthBackendKind:
        return AuthBackendKind.CLAUDE_SUBSCRIPTION

    def invalidate_probe_cache(self) -> None:
        self._cached = None

    async def probe(self) -> AuthProbeResult:
        if self._cached is not None:
            return self._cached
        cli = self._locator()
        if cli is None:
            self._cached = AuthProbeResult(
                detected=False,
                authenticated=False,
                detail="claude CLI not found on PATH or in the SDK bundle",
            )
            return self._cached

        try:
            async with asyncio.timeout(_PROBE_TIMEOUT_SECS):
                stdout, stderr, returncode = await _run_subprocess(
                    cli,
                    "auth",
                    "status",
                    "--json",
                )
        except TimeoutError:
            self._cached = AuthProbeResult(
                detected=True,
                authenticated=False,
                detail=None,
                error=f"claude auth status timed out after {_PROBE_TIMEOUT_SECS:.0f}s",
            )
            return self._cached
        except OSError as exc:
            self._cached = AuthProbeResult(
                detected=False,
                authenticated=False,
                detail=None,
                error=f"failed to invoke {cli}: {exc}",
            )
            return self._cached

        if returncode != 0:
            self._cached = AuthProbeResult(
                detected=True,
                authenticated=False,
                detail=None,
                error=stderr.strip() or f"claude auth status exited {returncode}",
            )
            return self._cached

        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError as exc:
            self._cached = AuthProbeResult(
                detected=True,
                authenticated=False,
                detail=None,
                error=f"could not parse claude auth status JSON: {exc}",
            )
            return self._cached

        logged_in = bool(payload.get("loggedIn"))
        auth_method = payload.get("authMethod")
        api_provider = payload.get("apiProvider")
        if logged_in:
            detail_parts = ["Claude subscription"]
            if auth_method:
                detail_parts.append(f"({auth_method})")
            if api_provider and api_provider != "firstParty":
                detail_parts.append(f"via {api_provider}")
            self._cached = AuthProbeResult(
                detected=True,
                authenticated=True,
                detail=" ".join(detail_parts),
            )
        else:
            self._cached = AuthProbeResult(
                detected=True,
                authenticated=False,
                detail="claude CLI is installed but not logged in",
            )
        return self._cached

    async def ensure_ready(self) -> None:
        result = await self.probe()
        if not result.detected:
            raise AuthBackendNotDetectedError(
                result.error or result.detail or "claude CLI not detected"
            )
        if not result.authenticated:
            raise AuthBackendNotAuthenticatedError(
                result.error or result.detail or "claude CLI not authenticated"
            )

    async def token(self) -> str | None:
        # Subscription auth: the bundled CLI owns the OAuth token.
        # Returning None signals the provider to leave ANTHROPIC_API_KEY
        # unset, which is what makes the CLI use its own auth.
        return None


# Type alias for the API-key provider; either a literal string or an
# async callable for future hot-rotation. Resolved on each ``token()``
# call so a key change becomes visible without restarting the brain.
ApiKeyProvider = str | Callable[[], Awaitable[str | None]] | Callable[[], str | None]


class AnthropicApiAuth:
    """Auth backend that injects an Anthropic API key into the SDK
    spawn env. Secondary path per ADR-0020.

    The key may be provided literally (the v1-compatible path: the Rust
    core injects ``ANTHROPIC_API_KEY`` at brain spawn) or via a
    callable that fetches the current value lazily.
    """

    _ENV_VAR: str = "ANTHROPIC_API_KEY"

    def __init__(self, *, source: ApiKeyProvider | None = None) -> None:
        self._source = source if source is not None else self._read_env

    @property
    def kind(self) -> AuthBackendKind:
        return AuthBackendKind.ANTHROPIC_API

    @classmethod
    def _read_env(cls) -> str | None:
        value = os.environ.get(cls._ENV_VAR)
        return value if value else None

    async def _resolve(self) -> str | None:
        source = self._source
        if isinstance(source, str):
            return source
        result = source()
        if asyncio.iscoroutine(result):
            value: str | None = await result
            return value
        # Sync callables return either a string or None directly.
        return result  # type: ignore[return-value]

    async def probe(self) -> AuthProbeResult:
        try:
            value = await self._resolve()
        except Exception as exc:
            return AuthProbeResult(
                detected=True,
                authenticated=False,
                detail=None,
                error=f"failed to read {self._ENV_VAR}: {exc}",
            )

        if value:
            return AuthProbeResult(
                detected=True,
                authenticated=True,
                detail="Anthropic API key on file",
            )
        return AuthProbeResult(
            detected=True,
            authenticated=False,
            detail=f"no {self._ENV_VAR} set",
        )

    async def ensure_ready(self) -> None:
        result = await self.probe()
        if not result.authenticated:
            raise AuthBackendNotAuthenticatedError(
                result.detail or result.error or "no API key on file"
            )

    async def token(self) -> str | None:
        try:
            return await self._resolve()
        except Exception as exc:
            raise AuthBackendError(f"failed to read {self._ENV_VAR}: {exc}") from exc


async def _run_subprocess(
    program: str,
    *args: str,
) -> tuple[str, str, int]:
    """Run ``program`` with ``args`` and return ``(stdout, stderr, rc)``.

    Wrap the call in ``async with asyncio.timeout(...)`` for a deadline;
    on timeout, the cancellation propagates here, the process is killed
    and reaped, and the cancellation is re-raised as ``TimeoutError`` by
    the timeout context. ``OSError`` covers the can't-invoke case.
    """
    process = await asyncio.create_subprocess_exec(
        program,
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await process.communicate()
    except (asyncio.CancelledError, TimeoutError):
        process.kill()
        await process.wait()
        raise
    return (
        stdout_b.decode("utf-8", errors="replace"),
        stderr_b.decode("utf-8", errors="replace"),
        process.returncode if process.returncode is not None else -1,
    )
