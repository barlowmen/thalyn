"""Tests for the v2 IPC stub registration.

Verifies that every registered stub method raises ``NOT_IMPLEMENTED``
with a clear message, and that the registration set covers the
methods the build plan names as v0.20-scaffolded entry points.
"""

from __future__ import annotations

from typing import Any

import pytest
from thalyn_brain.rpc import NOT_IMPLEMENTED, Dispatcher
from thalyn_brain.v2_stubs_rpc import register_v2_stubs


class _DropNotify:
    """Notifier Protocol stand-in for tests that don't care about
    server-initiated notifications."""

    async def __call__(self, method: str, params: Any) -> None:
        return None


_drop_notify = _DropNotify()


@pytest.mark.parametrize(
    "method",
    [
        "project.classify",
    ],
)
async def test_stub_raises_not_implemented(method: str) -> None:
    dispatcher = Dispatcher()
    register_v2_stubs(dispatcher)
    response = await dispatcher.handle(
        {"jsonrpc": "2.0", "id": 1, "method": method, "params": {}},
        notify=_drop_notify,
    )
    assert response is not None
    assert "error" in response
    error = response["error"]
    assert isinstance(error, dict)
    assert error["code"] == NOT_IMPLEMENTED
    assert method in str(error["message"])


async def test_double_registration_errors() -> None:
    dispatcher = Dispatcher()
    register_v2_stubs(dispatcher)
    with pytest.raises(ValueError, match="already registered"):
        register_v2_stubs(dispatcher)
