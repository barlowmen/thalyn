"""Watchdog that exits the brain when the parent process disappears.

The Rust supervisor's `kill_on_drop(true)` covers the happy-path
shutdown, and `serve_stdio` exits cleanly on stdin EOF when the
parent's pipe closes. The watchdog is the third line of defense:
if the brain is stuck in a long-running async path (a hefty migration,
a wedged subprocess, an import-time hang) when the parent dies, the
stdin readline never gets a chance to surface EOF and the brain can
outlive the desktop app — wedging the next launch with a stale port
file or a still-held SQLite lock. ADR-0018 calls this out as the
class of bug PyInstaller-bundled sidecars are known to hit.

Posix only for now (macOS + Linux). On Windows `os.getppid()` exists
but doesn't reassign to a sentinel when the parent dies, so the same
trick won't fire — `OpenProcess` + `WaitForSingleObject` is the right
shape there and lands when Windows packaging follows up.
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Awaitable, Callable

# Two-second poll is plenty: the watchdog only needs to fire fast
# enough that a user kill-and-relaunch doesn't observe the prior
# brain still holding resources, not in real time.
_POLL_INTERVAL_SEC = 2.0


async def watch_parent(
    *,
    poll_interval: float = _POLL_INTERVAL_SEC,
    on_orphan: Callable[[], None] | None = None,
    getppid: Callable[[], int] = os.getppid,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Poll `os.getppid()`; exit the process if it ever changes.

    The arguments exist so the test can drive a fake clock and a
    fake parent-pid sequence — production callers pass nothing.
    """
    if sys.platform == "win32":
        return
    original_parent = getppid()
    while True:
        await sleep(poll_interval)
        if getppid() != original_parent:
            if on_orphan is not None:
                on_orphan()
            else:
                # `os._exit` skips atexit handlers — appropriate
                # because the parent is already gone and any
                # graceful-shutdown code that touches IPC will
                # block on a dead pipe.
                os._exit(0)
            return
