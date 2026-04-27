"""User-supplied Sentry crash reporting opt-in."""

from __future__ import annotations

from typing import Any

import pytest
import sentry_sdk
from thalyn_brain.error_reporting import (
    ENV_VAR,
    init_sentry,
    is_enabled,
    reset,
)


@pytest.fixture(autouse=True)
def reset_between_tests() -> Any:
    yield
    reset()


def test_no_dsn_no_init(monkeypatch: Any) -> None:
    monkeypatch.delenv(ENV_VAR, raising=False)
    assert init_sentry() is False
    assert is_enabled() is False


def test_explicit_dsn_initialises(monkeypatch: Any) -> None:
    monkeypatch.delenv(ENV_VAR, raising=False)
    # Sentry's DSN parser requires a structurally valid URL but does
    # not actually attempt network calls during init when the project
    # id is unreachable.
    assert init_sentry(dsn="https://public-key@example.invalid/1") is True
    assert is_enabled() is True
    assert sentry_sdk.get_client().is_active()


def test_env_var_initialises(monkeypatch: Any) -> None:
    monkeypatch.setenv(ENV_VAR, "https://public-key@example.invalid/1")
    assert init_sentry() is True
    assert is_enabled() is True


def test_blank_dsn_treated_as_unset(monkeypatch: Any) -> None:
    monkeypatch.setenv(ENV_VAR, "   ")
    assert init_sentry() is False
    assert is_enabled() is False


def test_init_is_idempotent(monkeypatch: Any) -> None:
    monkeypatch.setenv(ENV_VAR, "https://public-key@example.invalid/1")
    assert init_sentry() is True
    # Second call is a silent no-op even if a different DSN is passed.
    assert init_sentry(dsn="https://other-key@example.invalid/2") is True
    assert is_enabled() is True


def test_low_severity_breadcrumbs_dropped(monkeypatch: Any) -> None:
    """The before_send hook strips DEBUG / INFO breadcrumbs so the
    user's quota isn't burned on noise from third-party libs."""
    monkeypatch.setenv(ENV_VAR, "https://public-key@example.invalid/1")
    init_sentry()

    # Reach the registered before_send via the live client options.
    client = sentry_sdk.get_client()
    assert client.is_active()
    before_send = client.options.get("before_send")
    assert before_send is not None

    event = {
        "breadcrumbs": {
            "values": [
                {"level": "debug", "message": "noisy debug"},
                {"level": "info", "message": "boring info"},
                {"level": "warning", "message": "a real warning"},
                {"level": "error", "message": "an error"},
            ]
        }
    }
    cleaned = before_send(event, {})
    assert cleaned is not None
    levels = [b["level"] for b in cleaned["breadcrumbs"]["values"]]
    assert levels == ["warning", "error"]
