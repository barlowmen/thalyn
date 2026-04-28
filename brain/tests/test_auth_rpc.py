"""Tests for the auth-backend RPC surface."""

from __future__ import annotations

from typing import Any

import pytest
from thalyn_brain.auth_registry import AuthBackendRegistry
from thalyn_brain.auth_rpc import register_auth_methods
from thalyn_brain.provider.auth import (
    AuthBackendKind,
    AuthProbeResult,
)
from thalyn_brain.rpc import INVALID_PARAMS, Dispatcher


class _FakeBackend:
    """Conforming backend that returns a scripted probe outcome.

    Tracks ``probe`` call counts so tests can verify the registry
    re-runs probes when ``auth.probe`` is invoked explicitly."""

    def __init__(
        self,
        kind: AuthBackendKind,
        *,
        result: AuthProbeResult | None = None,
        token: str | None = None,
    ) -> None:
        self._kind = kind
        self._result = result or AuthProbeResult(
            detected=True,
            authenticated=True,
            detail=f"fake {kind.value}",
        )
        self._token = token
        self.probe_calls = 0

    @property
    def kind(self) -> AuthBackendKind:
        return self._kind

    async def probe(self) -> AuthProbeResult:
        self.probe_calls += 1
        return self._result

    async def ensure_ready(self) -> None:
        return None

    async def token(self) -> str | None:
        return self._token


class _DropNotify:
    async def __call__(self, method: str, params: Any) -> None:
        return None


_drop_notify = _DropNotify()


def _registry_with_fakes(
    *,
    active_kind: AuthBackendKind = AuthBackendKind.CLAUDE_SUBSCRIPTION,
    overrides: dict[AuthBackendKind, _FakeBackend] | None = None,
) -> tuple[AuthBackendRegistry, dict[AuthBackendKind, _FakeBackend]]:
    """Build a registry whose factories return fakes."""
    fakes: dict[AuthBackendKind, _FakeBackend] = {}
    factories: dict[AuthBackendKind, Any] = {}
    for kind in AuthBackendKind:
        fake = (overrides or {}).get(kind) or _FakeBackend(kind)
        fakes[kind] = fake
        factories[kind] = lambda f=fake: f
    registry = AuthBackendRegistry(
        active_kind=active_kind,
        factories=factories,
    )
    return registry, fakes


# ---------------------------------------------------------------------------
# AuthBackendRegistry
# ---------------------------------------------------------------------------


def test_registry_default_active_is_subscription() -> None:
    registry = AuthBackendRegistry()
    assert registry.active_kind == AuthBackendKind.CLAUDE_SUBSCRIPTION


def test_registry_lists_kinds_in_stable_order() -> None:
    registry = AuthBackendRegistry()
    kinds = registry.list_kinds()
    # Subscription / API key first to keep them at the top of the wizard.
    assert kinds[0] == AuthBackendKind.CLAUDE_SUBSCRIPTION
    assert kinds[1] == AuthBackendKind.ANTHROPIC_API
    assert set(kinds) == set(AuthBackendKind)


def test_registry_set_active_updates_active_kind() -> None:
    registry, _ = _registry_with_fakes()
    initial: AuthBackendKind = registry.active_kind
    assert initial == AuthBackendKind.CLAUDE_SUBSCRIPTION
    registry.set_active(AuthBackendKind.ANTHROPIC_API)
    after: AuthBackendKind = registry.active_kind
    assert after == AuthBackendKind.ANTHROPIC_API


def test_registry_instance_is_lazy_and_cached() -> None:
    constructed: list[AuthBackendKind] = []

    def factory_for(kind: AuthBackendKind) -> Any:
        def factory() -> _FakeBackend:
            constructed.append(kind)
            return _FakeBackend(kind)

        return factory

    factories = {kind: factory_for(kind) for kind in AuthBackendKind}
    registry = AuthBackendRegistry(
        active_kind=AuthBackendKind.OLLAMA,
        factories=factories,
    )
    # Nothing constructed yet.
    assert constructed == []
    # First access constructs.
    a = registry.instance(AuthBackendKind.OLLAMA)
    assert constructed == [AuthBackendKind.OLLAMA]
    # Second access reuses.
    b = registry.instance(AuthBackendKind.OLLAMA)
    assert a is b
    assert constructed == [AuthBackendKind.OLLAMA]


def test_registry_active_returns_current_instance() -> None:
    registry, fakes = _registry_with_fakes(active_kind=AuthBackendKind.OLLAMA)
    assert registry.active() is fakes[AuthBackendKind.OLLAMA]
    registry.set_active(AuthBackendKind.MLX)
    assert registry.active() is fakes[AuthBackendKind.MLX]


def test_registry_descriptor_marks_active() -> None:
    registry, _ = _registry_with_fakes(active_kind=AuthBackendKind.ANTHROPIC_API)
    sub = registry.descriptor(AuthBackendKind.CLAUDE_SUBSCRIPTION)
    api = registry.descriptor(AuthBackendKind.ANTHROPIC_API)
    assert sub["active"] is False
    assert api["active"] is True
    assert sub["kind"] == "claude_subscription"
    assert api["kind"] == "anthropic_api"


# ---------------------------------------------------------------------------
# auth.list
# ---------------------------------------------------------------------------


async def test_auth_list_returns_every_kind_with_probe_state() -> None:
    registry, fakes = _registry_with_fakes()
    dispatcher = Dispatcher()
    register_auth_methods(dispatcher, registry)

    response = await dispatcher.handle(
        {"jsonrpc": "2.0", "id": 1, "method": "auth.list", "params": {}},
        notify=_drop_notify,
    )

    assert response is not None
    payload = response["result"]
    assert isinstance(payload, dict)
    assert payload["activeKind"] == "claude_subscription"
    backends = payload["backends"]
    assert isinstance(backends, list)
    assert len(backends) == len(AuthBackendKind)
    kinds = [b["kind"] for b in backends]
    # Order is the registry's UI ordering.
    assert kinds[0] == "claude_subscription"
    assert kinds[1] == "anthropic_api"
    # Each entry carries a probe.
    for entry in backends:
        assert "probe" in entry
        assert "displayName" in entry
        assert "description" in entry
    # Every fake's probe ran exactly once.
    for fake in fakes.values():
        assert fake.probe_calls == 1


# ---------------------------------------------------------------------------
# auth.probe
# ---------------------------------------------------------------------------


async def test_auth_probe_runs_a_specific_kind() -> None:
    registry, fakes = _registry_with_fakes()
    dispatcher = Dispatcher()
    register_auth_methods(dispatcher, registry)

    response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "auth.probe",
            "params": {"kind": "anthropic_api"},
        },
        notify=_drop_notify,
    )

    assert response is not None
    payload = response["result"]
    assert isinstance(payload, dict)
    assert payload["kind"] == "anthropic_api"
    assert "probe" in payload
    assert fakes[AuthBackendKind.ANTHROPIC_API].probe_calls == 1
    # Only the one we asked about.
    assert fakes[AuthBackendKind.OLLAMA].probe_calls == 0


@pytest.mark.parametrize("missing", [{}, {"kind": ""}, {"kind": 123}])
async def test_auth_probe_rejects_invalid_kind_param(missing: dict[str, Any]) -> None:
    registry, _ = _registry_with_fakes()
    dispatcher = Dispatcher()
    register_auth_methods(dispatcher, registry)

    response = await dispatcher.handle(
        {"jsonrpc": "2.0", "id": 3, "method": "auth.probe", "params": missing},
        notify=_drop_notify,
    )

    assert response is not None
    error = response["error"]
    assert isinstance(error, dict)
    assert error["code"] == INVALID_PARAMS


async def test_auth_probe_rejects_unknown_kind() -> None:
    registry, _ = _registry_with_fakes()
    dispatcher = Dispatcher()
    register_auth_methods(dispatcher, registry)

    response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "auth.probe",
            "params": {"kind": "vertex_ai"},
        },
        notify=_drop_notify,
    )

    assert response is not None
    error = response["error"]
    assert isinstance(error, dict)
    assert error["code"] == INVALID_PARAMS
    assert "vertex_ai" in str(error["message"])


# ---------------------------------------------------------------------------
# auth.set
# ---------------------------------------------------------------------------


async def test_auth_set_changes_active_and_returns_fresh_probe() -> None:
    registry, fakes = _registry_with_fakes(active_kind=AuthBackendKind.CLAUDE_SUBSCRIPTION)
    dispatcher = Dispatcher()
    register_auth_methods(dispatcher, registry)

    response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "auth.set",
            "params": {"kind": "anthropic_api"},
        },
        notify=_drop_notify,
    )

    assert response is not None
    payload = response["result"]
    assert isinstance(payload, dict)
    assert payload["activeKind"] == "anthropic_api"
    assert "probe" in payload
    assert registry.active_kind == AuthBackendKind.ANTHROPIC_API
    # The fresh probe is read from the newly active backend.
    assert fakes[AuthBackendKind.ANTHROPIC_API].probe_calls == 1


async def test_auth_set_probe_reflects_unauthenticated_state() -> None:
    registry, _ = _registry_with_fakes(
        overrides={
            AuthBackendKind.ANTHROPIC_API: _FakeBackend(
                AuthBackendKind.ANTHROPIC_API,
                result=AuthProbeResult(
                    detected=True,
                    authenticated=False,
                    detail="no key",
                ),
            )
        }
    )
    dispatcher = Dispatcher()
    register_auth_methods(dispatcher, registry)

    response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "auth.set",
            "params": {"kind": "anthropic_api"},
        },
        notify=_drop_notify,
    )

    assert response is not None
    payload = response["result"]
    assert isinstance(payload, dict)
    probe = payload["probe"]
    assert isinstance(probe, dict)
    assert probe["authenticated"] is False
    assert probe["detail"] == "no key"


async def test_auth_set_with_unknown_kind_returns_invalid_params() -> None:
    registry, _ = _registry_with_fakes()
    dispatcher = Dispatcher()
    register_auth_methods(dispatcher, registry)

    response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "auth.set",
            "params": {"kind": "blah_compat"},
        },
        notify=_drop_notify,
    )

    assert response is not None
    error = response["error"]
    assert isinstance(error, dict)
    assert error["code"] == INVALID_PARAMS
