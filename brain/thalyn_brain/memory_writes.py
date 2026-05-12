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

Workers reach project memory only through ``record_worker_project_memory_write``.
The wrapper enforces F6.6: workers can't write ``personal`` memory
even by accident, and the lead through which the write flows
becomes part of the audit-log payload so the renderer can show
"Worker X wrote this via Lead Y".

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
    agent_id: str | None = None,
    via_lead_id: str | None = None,
    writer_role: str | None = None,
) -> MemoryEntry:
    """Record a memory write in both the store and the run's
    action log.

    ``notify`` is the orchestrator's audit-tee notifier — passing
    ``None`` skips the notification (useful for tests / direct API
    callers that aren't tied to a run). When a notifier is supplied,
    the audit-log entry carries the memory id so the renderer can
    deep-link to the entry from the inspector.

    ``via_lead_id`` and ``writer_role`` are optional provenance
    fields: when a worker writes through a lead, the lead's id and
    the role tag (``worker``) ride into the action-log payload so
    the user can drill into "Worker X wrote this through Lead Y".
    Brain-direct writes leave both unset and the payload omits the
    fields.
    """
    if not body.strip():
        raise ValueError("memory body is required")
    now_ms = int(time.time() * 1000)
    entry = MemoryEntry(
        memory_id=new_memory_id(),
        project_id=project_id,
        agent_id=agent_id,
        scope=scope,
        kind=kind,
        body=body,
        author=author,
        created_at_ms=now_ms,
        updated_at_ms=now_ms,
    )
    await store.insert(entry)

    if notify is not None:
        payload: dict[str, Any] = {
            "memoryId": entry.memory_id,
            "scope": entry.scope,
            "kindOfMemory": entry.kind,
            "author": entry.author,
            "preview": _preview(entry.body),
        }
        if via_lead_id is not None:
            payload["viaLeadId"] = via_lead_id
        if writer_role is not None:
            payload["writerRole"] = writer_role
        action = ActionLogEntry(
            at_ms=now_ms,
            kind="memory_write",
            payload=payload,
        )
        await notify(
            "run.action_log",
            {"runId": run_id, "entry": action.to_wire()},
        )

    return entry


async def record_worker_project_memory_write(
    store: MemoryStore,
    *,
    run_id: str,
    project_id: str,
    body: str,
    kind: str,
    worker_author: str,
    via_lead_id: str,
    notify: Notifier | None = None,
) -> MemoryEntry:
    """Worker → lead-mediated project-memory write.

    Workers cannot write ``personal`` memory and must not bypass
    the lead when adding to project memory (F6.6). This helper is
    the structured interface that enforces both rules: the scope
    is fixed at ``project``, the project id is required, and the
    audit-log payload records both the worker and the lead so the
    user can audit who wrote what.

    Raises ``ValueError`` when the project id is empty so a
    miswired caller fails loudly rather than landing an orphan
    project-scope row with no project.
    """
    if not project_id:
        raise ValueError("project_id is required for project-memory writes")
    if not via_lead_id:
        raise ValueError("via_lead_id is required for worker project-memory writes")
    return await record_memory_write(
        store,
        run_id=run_id,
        body=body,
        scope="project",
        kind=kind,
        author=worker_author,
        notify=notify,
        project_id=project_id,
        via_lead_id=via_lead_id,
        writer_role="worker",
    )


def _preview(body: str, *, max_chars: int = 240) -> str:
    if len(body) <= max_chars:
        return body
    return body[: max_chars - 1] + "…"


__all__ = ["record_memory_write", "record_worker_project_memory_write"]
