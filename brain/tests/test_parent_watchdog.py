"""Unit tests for the parent-process watchdog."""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest
from thalyn_brain.parent_watchdog import watch_parent


async def _no_sleep(_: float) -> None:
    return None


async def test_returns_immediately_on_windows() -> None:
    """On Windows the watchdog is a no-op until the cross-platform follow-on lands."""
    called: list[None] = []

    def fake_getppid() -> int:
        called.append(None)
        return 1

    with patch.object(sys, "platform", "win32"):
        await watch_parent(getppid=fake_getppid, sleep=_no_sleep)

    assert called == []


async def test_fires_on_orphan() -> None:
    """A changed parent pid triggers `on_orphan` and ends the loop."""
    parents = iter([100, 100, 1])
    fired: list[bool] = []

    await watch_parent(
        poll_interval=0.0,
        on_orphan=lambda: fired.append(True),
        getppid=lambda: next(parents),
        sleep=_no_sleep,
    )

    assert fired == [True]


async def test_does_not_fire_when_parent_stable() -> None:
    """Stable parent pid means the loop keeps going — caller cancels it."""
    iterations = 0

    def fake_getppid() -> int:
        nonlocal iterations
        iterations += 1
        if iterations > 5:
            # End the test by raising — production caller would
            # cancel the task instead. Bare RuntimeError so the
            # PEP 479 wrapping doesn't muddy the assertion.
            raise RuntimeError("stop")
        return 42

    fired: list[bool] = []

    with pytest.raises(RuntimeError, match="stop"):
        await watch_parent(
            poll_interval=0.0,
            on_orphan=lambda: fired.append(True),
            getppid=fake_getppid,
            sleep=_no_sleep,
        )

    assert fired == []
    assert iterations >= 5
