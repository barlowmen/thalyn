"""Tests for the v2 IPC stub registration.

The original v2 surface registered NOT_IMPLEMENTED stubs for every
later-stage entry point so the IPC contract existed from day one.
Each method has since landed as a real handler. The remaining
contract here is structural: ``register_v2_stubs`` must stay
importable and idempotent so future stages can re-introduce the
helper without touching the dispatcher's lifecycle, and a stub
that *does* land must raise ``NOT_IMPLEMENTED`` with the stage
name in the message so the renderer can surface a useful hint.
"""

from __future__ import annotations

from typing import Any

import pytest
from thalyn_brain.rpc import NOT_IMPLEMENTED, Dispatcher
from thalyn_brain.v2_stubs_rpc import _make_stub, register_v2_stubs


class _DropNotify:
    async def __call__(self, method: str, params: Any) -> None:
        return None


_drop_notify = _DropNotify()


async def test_register_v2_stubs_is_a_no_op_today() -> None:
    """Every original stub has landed as a real handler; the
    registrar should add zero methods today."""
    dispatcher = Dispatcher()
    register_v2_stubs(dispatcher)
    response = await dispatcher.handle(
        {"jsonrpc": "2.0", "id": 1, "method": "anything.unregistered", "params": {}},
        notify=_drop_notify,
    )
    assert response is not None
    assert "error" in response
    # Method-not-found rather than NOT_IMPLEMENTED because the stub
    # isn't there to give a structured signal — the helper is empty
    # by design.


async def test_make_stub_emits_not_implemented_with_stage_hint() -> None:
    """``_make_stub`` is the helper future stages reach for when
    they want a placeholder ahead of the real handler."""
    dispatcher = Dispatcher()
    handler = _make_stub("future.method", "the future stage")
    dispatcher.register("future.method", handler)
    response = await dispatcher.handle(
        {"jsonrpc": "2.0", "id": 1, "method": "future.method", "params": {}},
        notify=_drop_notify,
    )
    assert response is not None
    error = response["error"]
    assert error["code"] == NOT_IMPLEMENTED
    assert "future.method" in str(error["message"])
    assert "future stage" in str(error["message"])


async def test_double_registration_is_idempotent_when_empty() -> None:
    """The empty-stubs path must be re-runnable so callers don't
    have to track whether they already registered. (The helper
    raises only when there's a duplicate concrete method to add.)"""
    dispatcher = Dispatcher()
    register_v2_stubs(dispatcher)
    register_v2_stubs(dispatcher)


@pytest.mark.parametrize(
    "method",
    [
        "future.example_a",
        "future.example_b",
    ],
)
async def test_concrete_stubs_block_double_register(method: str) -> None:
    """When a concrete stub *is* present, the dispatcher's
    duplicate-check kicks in on the second registration so the
    real handler can't silently land alongside the stub."""
    dispatcher = Dispatcher()
    dispatcher.register(method, _make_stub(method, "the future stage"))
    with pytest.raises(ValueError, match="already registered"):
        dispatcher.register(method, _make_stub(method, "the future stage"))
