"""Tests for the auth-backend Protocol surface."""

from __future__ import annotations

import json

import pytest
from thalyn_brain.provider.auth import (
    AuthBackend,
    AuthBackendError,
    AuthBackendKind,
    AuthBackendNotAuthenticatedError,
    AuthBackendNotDetectedError,
    AuthProbeResult,
)


class _ReadyBackend:
    """Tiny conforming implementation used to verify the Protocol."""

    def __init__(self, kind: AuthBackendKind, token_value: str | None) -> None:
        self._kind = kind
        self._token = token_value

    @property
    def kind(self) -> AuthBackendKind:
        return self._kind

    async def probe(self) -> AuthProbeResult:
        return AuthProbeResult(
            detected=True,
            authenticated=True,
            detail="ready",
        )

    async def ensure_ready(self) -> None:
        return None

    async def token(self) -> str | None:
        return self._token


def test_auth_backend_kind_enum_has_six_members() -> None:
    assert {kind.value for kind in AuthBackendKind} == {
        "claude_subscription",
        "anthropic_api",
        "openai_compat",
        "ollama",
        "llama_cpp",
        "mlx",
    }


def test_auth_backend_kind_round_trips_via_strings() -> None:
    for kind in AuthBackendKind:
        assert AuthBackendKind(kind.value) is kind


def test_concrete_backend_satisfies_runtime_checkable_protocol() -> None:
    backend = _ReadyBackend(AuthBackendKind.CLAUDE_SUBSCRIPTION, token_value=None)
    assert isinstance(backend, AuthBackend)


async def test_token_none_signals_subscription_auth() -> None:
    backend = _ReadyBackend(AuthBackendKind.CLAUDE_SUBSCRIPTION, token_value=None)
    assert await backend.token() is None


async def test_token_string_signals_api_key_auth() -> None:
    backend = _ReadyBackend(AuthBackendKind.ANTHROPIC_API, token_value="sk-test")
    assert await backend.token() == "sk-test"


def test_probe_result_to_wire_matches_camel_case_contract() -> None:
    result = AuthProbeResult(
        detected=True,
        authenticated=False,
        detail="logged out",
    )
    wire = result.to_wire()
    # camelCase keys mirror the Rust serde rendering.
    assert wire["detected"] is True
    assert wire["authenticated"] is False
    assert wire["detail"] == "logged out"
    assert wire["error"] is None
    # JSON-serializable.
    assert json.loads(json.dumps(wire)) == wire


def test_probe_result_states_are_distinguishable() -> None:
    ready = AuthProbeResult(detected=True, authenticated=True, detail="ok")
    no_creds = AuthProbeResult(detected=True, authenticated=False, detail="no key")
    not_installed = AuthProbeResult(detected=False, authenticated=False, detail="cli missing")
    errored = AuthProbeResult(
        detected=False,
        authenticated=False,
        detail=None,
        error="subprocess died",
    )
    samples = (ready, no_creds, not_installed, errored)
    states = {(r.detected, r.authenticated, r.error is None) for r in samples}
    assert len(states) == 4


def test_error_hierarchy_lets_callers_match_specific_failures() -> None:
    not_detected = AuthBackendNotDetectedError("cli missing")
    not_auth = AuthBackendNotAuthenticatedError("logged out")

    assert isinstance(not_detected, AuthBackendError)
    assert isinstance(not_auth, AuthBackendError)
    with pytest.raises(AuthBackendNotDetectedError):
        raise not_detected
    with pytest.raises(AuthBackendNotAuthenticatedError):
        raise not_auth
