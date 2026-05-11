"""Pending hard-gate actions for the conversational surface.

When the matcher pipeline hits a hard-gated action (per F12.5 —
publish, send money, send messages on the user's behalf), the
registry's executor must not run on the same turn that surfaced the
intent. ``PendingActionStore`` parks the parsed inputs against a
fresh approval id, emits ``action.approval_required`` so the
renderer can surface its dialog, and waits for the resolution
through ``action.approve`` / ``action.reject``.

The store is in-memory on purpose: pending actions are short-lived
(seconds to minutes), tied to a single eternal-thread session, and
must not survive a brain restart — a hard-gated action staged before
a crash is one the user must re-confirm intent on after the restart.
Persistence belongs to the per-run ``ApprovalsStore``, which gates
agent-run plans rather than chat-initiated actions.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


def new_action_approval_id() -> str:
    return f"actappr_{uuid.uuid4().hex}"


PENDING = "pending"
APPROVED = "approved"
REJECTED = "rejected"
RESOLVED = frozenset({APPROVED, REJECTED})


@dataclass
class PendingAction:
    """One staged hard-gate action awaiting user resolution.

    ``thread_id`` and ``turn_id`` carry the eternal-thread anchor so
    the renderer can show "you asked me to do this on turn X" in the
    dialog. ``hard_gate_kind`` mirrors the action's metadata field
    for filter/grouping in the dialog UI.
    """

    approval_id: str
    action_name: str
    inputs: dict[str, Any]
    hard_gate_kind: str | None
    preview: str | None
    thread_id: str
    turn_id: str
    requested_at_ms: int
    status: str = PENDING
    resolved_at_ms: int | None = None

    def to_wire(self) -> dict[str, Any]:
        return {
            "approvalId": self.approval_id,
            "actionName": self.action_name,
            "inputs": dict(self.inputs),
            "hardGateKind": self.hard_gate_kind,
            "preview": self.preview,
            "threadId": self.thread_id,
            "turnId": self.turn_id,
            "requestedAtMs": self.requested_at_ms,
            "status": self.status,
            "resolvedAtMs": self.resolved_at_ms,
        }


class PendingActionStore:
    """In-memory index of hard-gate actions awaiting user approval."""

    def __init__(self) -> None:
        self._entries: dict[str, PendingAction] = {}
        self._lock = asyncio.Lock()

    async def stage(
        self,
        *,
        action_name: str,
        inputs: Mapping[str, Any],
        hard_gate_kind: str | None,
        preview: str | None,
        thread_id: str,
        turn_id: str,
    ) -> PendingAction:
        entry = PendingAction(
            approval_id=new_action_approval_id(),
            action_name=action_name,
            inputs=dict(inputs),
            hard_gate_kind=hard_gate_kind,
            preview=preview,
            thread_id=thread_id,
            turn_id=turn_id,
            requested_at_ms=int(time.time() * 1000),
        )
        async with self._lock:
            self._entries[entry.approval_id] = entry
        return entry

    async def get(self, approval_id: str) -> PendingAction | None:
        async with self._lock:
            return self._entries.get(approval_id)

    async def list_pending(self) -> list[PendingAction]:
        async with self._lock:
            return [e for e in self._entries.values() if e.status == PENDING]

    async def resolve(self, approval_id: str, *, status: str) -> PendingAction | None:
        """Flip the entry to ``approved`` / ``rejected`` and stamp.

        Returns the resolved entry, or ``None`` when the id is
        unknown or already resolved (so the caller can surface a
        TOCTOU race in the dialog).
        """

        if status not in RESOLVED:
            raise ValueError(f"invalid resolution status: {status}")
        async with self._lock:
            entry = self._entries.get(approval_id)
            if entry is None or entry.status != PENDING:
                return None
            entry.status = status
            entry.resolved_at_ms = int(time.time() * 1000)
            return entry

    async def discard(self, approval_id: str) -> bool:
        async with self._lock:
            return self._entries.pop(approval_id, None) is not None


@dataclass(frozen=True)
class ActionApprovalRequiredEvent:
    """The shape pushed over ``action.approval_required``.

    Carries everything the dialog needs to render: which action, the
    inputs the user is about to authorise, a human-readable preview,
    and the turn id so the dialog can deep-link back to chat.
    """

    approval_id: str
    action_name: str
    hard_gate_kind: str | None
    preview: str | None
    inputs: Mapping[str, Any]
    thread_id: str
    turn_id: str
    requested_at_ms: int = field(default_factory=lambda: int(time.time() * 1000))

    def to_wire(self) -> dict[str, Any]:
        return {
            "approvalId": self.approval_id,
            "actionName": self.action_name,
            "hardGateKind": self.hard_gate_kind,
            "preview": self.preview,
            "inputs": dict(self.inputs),
            "threadId": self.thread_id,
            "turnId": self.turn_id,
            "requestedAtMs": self.requested_at_ms,
        }


__all__ = [
    "APPROVED",
    "PENDING",
    "REJECTED",
    "RESOLVED",
    "ActionApprovalRequiredEvent",
    "PendingAction",
    "PendingActionStore",
    "new_action_approval_id",
]
