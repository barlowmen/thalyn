"""Brain-side observer for renderer-owned terminal sessions.

The terminal pty itself lives in the Tauri host (portable-pty). Tauri
pushes every byte the pty produces into the brain via the
``terminal.observe`` JSON-RPC method, and the observer keeps a per-
session ring buffer the agent's tools can read back.

The point: agents living in the brain need to "attach" to whatever
shell the user has open in the editor surface — close commands, see
output, decide what to do next — without taking ownership of the pty
itself. This module is the brain's seam for that. The actual tool
that exposes it to the SDK lives next to the agent options.
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

# Each session's ring buffer caps at this many characters. Tuned to
# match the renderer's snapshot size so an agent reading the brain
# observer sees the same window the user sees in xterm.
PER_SESSION_BUFFER_CHARS = 16 * 1024


Listener = Callable[[str, str], Awaitable[None]]
"""Callback for live terminal output. Args: (session_id, data)."""


@dataclass
class TerminalSnapshot:
    """What an agent attaches to: the current buffer + metadata."""

    session_id: str
    data: str
    last_seq: int
    updated_at_ms: int

    def to_wire(self) -> dict[str, Any]:
        return {
            "sessionId": self.session_id,
            "data": self.data,
            "lastSeq": self.last_seq,
            "updatedAtMs": self.updated_at_ms,
        }


@dataclass
class _SessionState:
    buffer: list[str] = field(default_factory=list)
    last_seq: int = 0
    updated_at_ms: int = 0


class TerminalObserver:
    """Per-session ring-buffered view of terminal output."""

    def __init__(self) -> None:
        self._sessions: OrderedDict[str, _SessionState] = OrderedDict()
        self._listeners: set[Listener] = set()
        self._lock = asyncio.Lock()

    async def observe(self, session_id: str, seq: int, data: str) -> None:
        """Record one chunk of terminal output. ``seq`` is the
        monotonic sequence id from the pty reader; chunks that
        arrive out of order are still accepted (we trust the
        producer to send them in order)."""

        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                session = _SessionState()
                self._sessions[session_id] = session
            else:
                # Move-to-end so list_sessions() can report most-
                # recently-active first.
                self._sessions.move_to_end(session_id)
            session.buffer.append(data)
            self._coalesce(session)
            session.last_seq = seq
            session.updated_at_ms = int(time.time() * 1000)

        for listener in tuple(self._listeners):
            try:
                await listener(session_id, data)
            except Exception:
                continue

    async def forget(self, session_id: str) -> bool:
        """Drop bookkeeping for a closed session."""

        async with self._lock:
            return self._sessions.pop(session_id, None) is not None

    async def read(
        self,
        session_id: str | None = None,
        *,
        max_chars: int = PER_SESSION_BUFFER_CHARS,
    ) -> TerminalSnapshot | None:
        """Return a snapshot for ``session_id`` (or the most-recent
        session if ``None``). Returns ``None`` if there's no such
        session."""

        async with self._lock:
            session, target_id = self._resolve(session_id)
            if session is None or target_id is None:
                return None
            joined = "".join(session.buffer)
            if max_chars < len(joined):
                joined = joined[-max_chars:]
            return TerminalSnapshot(
                session_id=target_id,
                data=joined,
                last_seq=session.last_seq,
                updated_at_ms=session.updated_at_ms,
            )

    async def list_sessions(self) -> list[dict[str, Any]]:
        async with self._lock:
            return [
                {
                    "sessionId": session_id,
                    "lastSeq": state.last_seq,
                    "updatedAtMs": state.updated_at_ms,
                    "bufferedChars": sum(len(part) for part in state.buffer),
                }
                for session_id, state in reversed(self._sessions.items())
            ]

    def add_listener(self, listener: Listener) -> Callable[[], None]:
        self._listeners.add(listener)

        def remove() -> None:
            self._listeners.discard(listener)

        return remove

    # --- internals -----------------------------------------------------

    def _resolve(self, session_id: str | None) -> tuple[_SessionState | None, str | None]:
        if session_id is not None:
            session = self._sessions.get(session_id)
            return session, session_id if session else None
        if not self._sessions:
            return None, None
        # OrderedDict preserves insertion + move_to_end order; the
        # last item is the most recently active session.
        last_id = next(reversed(self._sessions))
        return self._sessions[last_id], last_id

    def _coalesce(self, session: _SessionState) -> None:
        """Trim the buffer to the per-session cap, joining chunks
        when the parts list grows long enough that lookups get slow."""

        total = sum(len(part) for part in session.buffer)
        if total > PER_SESSION_BUFFER_CHARS:
            joined = "".join(session.buffer)
            session.buffer = [joined[-PER_SESSION_BUFFER_CHARS:]]
        elif len(session.buffer) > 64:
            session.buffer = ["".join(session.buffer)]
