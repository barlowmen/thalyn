"""Structured memory-write surface — no silent writes.

Every memory write the brain performs (whether agent-driven or
user-initiated through the renderer) flows through
``record_memory_write``. The function does two things:

1. Insert the entry through the ``MemoryStore`` so it lands in
   ``app.db`` and is visible to every subsequent ``memory.list``
   call.
2. Emit a ``run.action_log`` notification with ``kind:
   "memory_write"`` so the inspector picks the write up live and
   the per-run audit log records it. The user reviews who wrote
   what and when without having to re-read the chat log.

The shape mirrors the requirement in F8.4 / `01-requirements.md`:
no silent profile-building. If a write doesn't go through this
helper, the audit log won't see it.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from thalyn_brain.memory import MemoryEntry, MemoryStore, new_memory_id
from thalyn_brain.orchestration.state import ActionLogEntry

Notifier = Callable[[str, Any], Awaitable[None]]


async def record_memory_write(
    store: MemoryStore,
    *,
    run_id: str,
    body: str,
    scope: str,
    kind: str,
    author: str,
    notify: Notifier | None = None,
    project_id: str | None = None,
) -> MemoryEntry:
    """Record a memory write in both the store and the run's
    action log.

    ``notify`` is the orchestrator's audit-tee notifier — passing
    ``None`` skips the notification (useful for tests / direct API
    callers that aren't tied to a run). When a notifier is supplied,
    the audit-log entry carries the memory id so the renderer can
    deep-link to the entry from the inspector.
    """
    if not body.strip():
        raise ValueError("memory body is required")
    now_ms = int(time.time() * 1000)
    entry = MemoryEntry(
        memory_id=new_memory_id(),
        project_id=project_id,
        scope=scope,
        kind=kind,
        body=body,
        author=author,
        created_at_ms=now_ms,
        updated_at_ms=now_ms,
    )
    await store.insert(entry)

    if notify is not None:
        action = ActionLogEntry(
            at_ms=now_ms,
            kind="memory_write",
            payload={
                "memoryId": entry.memory_id,
                "scope": entry.scope,
                "kindOfMemory": entry.kind,
                "author": entry.author,
                "preview": _preview(entry.body),
            },
        )
        await notify(
            "run.action_log",
            {"runId": run_id, "entry": action.to_wire()},
        )

    return entry


def _preview(body: str, *, max_chars: int = 240) -> str:
    if len(body) <= max_chars:
        return body
    return body[: max_chars - 1] + "…"


__all__ = ["record_memory_write"]
