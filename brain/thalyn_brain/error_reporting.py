"""User-supplied Sentry crash reporting for the brain sidecar.

Per `01-requirements.md` F10.3 / OQ-13, Thalyn never sends telemetry
to a server it operates. The user can opt into error reporting by
pasting their **own** Sentry DSN; crashes then route to their Sentry
project via ``sentry-sdk``. Without a DSN, nothing is initialised
and ``sentry-sdk`` is effectively dead weight.

The DSN reaches us as the ``THALYN_SENTRY_DSN`` env var, set by the
Rust core when it spawns the brain (the core reads the DSN from the
OS keychain via the existing ``SecretsManager`` so it never lives in
a config file). We do **not** initialise Sentry from any other
source — there is no implicit network surface.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import sentry_sdk

ENV_VAR = "THALYN_SENTRY_DSN"
ENV_RELEASE = "THALYN_SENTRY_RELEASE"
ENV_ENV = "THALYN_SENTRY_ENVIRONMENT"

_initialised = False


def init_sentry(*, dsn: str | None = None) -> bool:
    """Initialise crash reporting if a DSN is available.

    Returns ``True`` if a Sentry client was set up; ``False`` if no
    DSN was provided (no-op). Idempotent — repeat calls with a DSN
    are silently skipped.
    """
    global _initialised
    if _initialised:
        return True

    effective_dsn = dsn if dsn is not None else os.environ.get(ENV_VAR, "").strip()
    if not effective_dsn:
        return False

    sentry_sdk.init(
        dsn=effective_dsn,
        # Errors only — no performance / profiling traffic. The user
        # opted into crash reporting; we don't expand the scope.
        traces_sample_rate=0.0,
        profiles_sample_rate=0.0,
        # We don't ship the brain version to Sentry by default —
        # release tagging is opt-in via THALYN_SENTRY_RELEASE so the
        # user's project isn't cluttered with our internal versions.
        release=os.environ.get(ENV_RELEASE) or None,
        environment=os.environ.get(ENV_ENV) or None,
        # Explicit; sentry-sdk's default is True but we want to be
        # loud about which integrations are on.
        send_default_pii=False,
        # Don't auto-enable the FastAPI / Flask / Django integrations
        # — the brain doesn't ship any of those.
        auto_enabling_integrations=False,
        # Drop logs below WARNING so we don't burn quota on
        # debug-level INFO messages from third-party libs.
        before_send=_drop_low_severity_breadcrumbs,
    )
    _initialised = True
    logging.getLogger(__name__).info("Sentry crash reporting enabled")
    return True


def is_enabled() -> bool:
    """True iff :func:`init_sentry` actually attached a client."""
    return _initialised


def reset() -> None:
    """Tear down the current client. Tests use this; production does not."""
    global _initialised
    if not _initialised:
        return
    client = sentry_sdk.get_client()
    if client.is_active():
        client.close()
    _initialised = False


def _drop_low_severity_breadcrumbs(event: Any, _hint: Any) -> Any:
    """Strip BREADCRUMBS that are below WARNING.

    Keeps the user's Sentry project signal high — we don't want
    every DEBUG line from the Anthropic SDK or LangGraph clogging
    error events.
    """
    breadcrumbs = event.get("breadcrumbs")
    if isinstance(breadcrumbs, dict):
        values = breadcrumbs.get("values")
        if isinstance(values, list):
            kept = [
                b for b in values if isinstance(b, dict) and b.get("level") not in {"debug", "info"}
            ]
            breadcrumbs["values"] = kept
    return event
