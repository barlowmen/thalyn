"""Agent-facing terminal-attach tool.

Wraps :class:`TerminalObserver` in the shape the Claude Agent SDK
(and other providers) expect for a callable tool. Implementations
register this with their underlying SDK in a follow-up commit; for
v0.12 we land the spec + Python entry point so the wiring is in
place and an agent can read terminal state through a stable
contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from thalyn_brain.terminal_observer import (
    PER_SESSION_BUFFER_CHARS,
    TerminalObserver,
)

TOOL_NAME = "terminal_attach"
TOOL_DESCRIPTION = (
    "Attach to one of the user's terminal sessions and read its recent "
    "output. Use sessionId to target a specific terminal, or omit it to "
    "attach to the most recently active one. Returns the last few KB of "
    "output as a single string. The tool is read-only: it does not "
    "execute commands or write input."
)
TOOL_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "sessionId": {
            "type": "string",
            "description": (
                "Session id to attach to. When omitted, the most recently active terminal is used."
            ),
        },
        "maxChars": {
            "type": "integer",
            "minimum": 1,
            "maximum": PER_SESSION_BUFFER_CHARS,
            "description": (
                "Maximum number of characters of recent output to return. "
                f"Defaults to {PER_SESSION_BUFFER_CHARS}."
            ),
        },
    },
    "additionalProperties": False,
}


@dataclass(frozen=True)
class TerminalAttachResult:
    """One attach call's return value."""

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


class TerminalAttachUnavailable(RuntimeError):
    """Raised when no terminal sessions are open."""


async def terminal_attach(
    observer: TerminalObserver,
    *,
    session_id: str | None = None,
    max_chars: int = PER_SESSION_BUFFER_CHARS,
) -> TerminalAttachResult:
    """Read recent output for an existing terminal. Raises
    :class:`TerminalAttachUnavailable` if there are no live
    sessions."""

    snapshot = await observer.read(session_id, max_chars=max_chars)
    if snapshot is None:
        raise TerminalAttachUnavailable(
            "no terminal sessions are open"
            if session_id is None
            else f"unknown terminal session: {session_id}"
        )
    return TerminalAttachResult(
        session_id=snapshot.session_id,
        data=snapshot.data,
        last_seq=snapshot.last_seq,
        updated_at_ms=snapshot.updated_at_ms,
    )


def tool_spec() -> dict[str, Any]:
    """Static spec the agent SDK consumes when registering tools."""

    return {
        "name": TOOL_NAME,
        "description": TOOL_DESCRIPTION,
        "input_schema": TOOL_INPUT_SCHEMA,
    }
