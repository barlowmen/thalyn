"""Zero-egress: with no observability env vars set, the brain must
not attempt any network calls when initialised.

The default-off promise is in `01-requirements.md` F10.3 and NFR4.
This test enforces it at runtime by disabling sockets globally with
``pytest-socket`` and confirming that init_tracer + init_sentry
bring up the brain without crashing — i.e., neither code path tries
to connect to anything.
"""

from __future__ import annotations

from typing import Any

import pytest
import sentry_sdk
from pytest_socket import disable_socket, enable_socket  # type: ignore[import-untyped]
from thalyn_brain.error_reporting import init_sentry, is_enabled, reset
from thalyn_brain.tracing import (
    THALYN_RUN_ID,
    init_tracer,
    llm_call_span,
    run_span,
)


@pytest.fixture(autouse=True)
def block_all_sockets() -> Any:
    """Force socket-level egress to fail loudly during the test."""
    disable_socket()
    yield
    enable_socket()
    reset()


def test_default_init_makes_no_network_calls(monkeypatch: Any) -> None:
    """The smoke: with no env vars set, init_tracer + init_sentry +
    a synthesised run + LLM span all complete without trying to
    open a single socket. ``pytest-socket`` would raise
    ``SocketBlockedError`` if anything tried to connect."""
    monkeypatch.delenv("THALYN_OTEL_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("THALYN_SENTRY_DSN", raising=False)

    init_tracer()
    sentry_attached = init_sentry()
    assert sentry_attached is False
    assert is_enabled() is False

    # Walk the same span surface the orchestration runner does — if
    # anything in the OTel pipeline tried to flush spans to a real
    # exporter, the socket block would surface here.
    with run_span(
        run_id="r_zero_egress",
        parent_run_id=None,
        provider_id="anthropic",
        session_id="sess",
    ) as span:
        span.set_attribute(THALYN_RUN_ID, "r_zero_egress")
        with llm_call_span(provider_id="anthropic", model="claude-sonnet-4-6"):
            pass


def test_sentry_init_with_dsn_does_not_connect_during_init(monkeypatch: Any) -> None:
    """Sentry's ``init`` is non-blocking by design — it doesn't
    connect until the first event. We confirm the contract by setting
    a DSN, initialising, and asserting no network call ran during
    init itself.
    """
    monkeypatch.setenv("THALYN_SENTRY_DSN", "https://public-key@example.invalid/1")
    assert init_sentry() is True
    # Sentry's transport is lazy — calling capture_message would try
    # to send and would hit the socket block. We don't capture
    # anything; we just confirm init itself stayed local.
    client = sentry_sdk.get_client()
    assert client.is_active()
