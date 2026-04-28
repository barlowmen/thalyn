"""Approval gate store.

Each approval gate (plan / push / publish / destructive / external_send /
depth / budget / drift / info_flow per ``02-architecture.md`` §5)
captures a pending decision against an agent run. The renderer surfaces
these inline in chat (and in the relevant drawer once drawers land);
resolving an approval flips ``status`` and stamps ``resolved_at_ms``.

v0.20 lands the storage; the gate-rendering UI and the drift-based
``info_flow`` gate land in their own subsequent stages.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from thalyn_brain.orchestration.storage import (
    apply_pending_migrations,
    default_data_dir,
)

GATE_KINDS = frozenset(
    {
        "plan",
        "push",
        "publish",
        "destructive",
        "external_send",
        "depth",
        "budget",
        "drift",
        "info_flow",
    }
)
APPROVAL_STATUSES = frozenset({"pending", "approved", "rejected"})


def new_approval_id() -> str:
    return f"appr_{uuid.uuid4().hex}"


@dataclass
class Approval:
    approval_id: str
    run_id: str
    gate_kind: str
    status: str
    context: dict[str, Any] | None
    requested_at_ms: int
    resolved_at_ms: int | None

    def to_wire(self) -> dict[str, Any]:
        return {
            "approvalId": self.approval_id,
            "runId": self.run_id,
            "gateKind": self.gate_kind,
            "status": self.status,
            "context": self.context,
            "requestedAtMs": self.requested_at_ms,
            "resolvedAtMs": self.resolved_at_ms,
        }


class ApprovalsStore:
    def __init__(self, *, data_dir: Path | None = None) -> None:
        base = data_dir or default_data_dir()
        apply_pending_migrations(data_dir=base)
        self._db_path = base / "app.db"
        self._lock = asyncio.Lock()

    def _open(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    async def insert(self, approval: Approval) -> None:
        if approval.gate_kind not in GATE_KINDS:
            raise ValueError(f"invalid gate kind: {approval.gate_kind}")
        if approval.status not in APPROVAL_STATUSES:
            raise ValueError(f"invalid approval status: {approval.status}")
        async with self._lock:
            await asyncio.to_thread(self._insert_sync, approval)

    def _insert_sync(self, approval: Approval) -> None:
        with self._open() as conn:
            conn.execute(
                """
                INSERT INTO approvals
                    (approval_id, run_id, gate_kind, status, context_json,
                     requested_at_ms, resolved_at_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    approval.approval_id,
                    approval.run_id,
                    approval.gate_kind,
                    approval.status,
                    json.dumps(approval.context) if approval.context is not None else None,
                    approval.requested_at_ms,
                    approval.resolved_at_ms,
                ),
            )

    async def get(self, approval_id: str) -> Approval | None:
        async with self._lock:
            return await asyncio.to_thread(self._get_sync, approval_id)

    def _get_sync(self, approval_id: str) -> Approval | None:
        with self._open() as conn:
            row = conn.execute(
                "SELECT * FROM approvals WHERE approval_id = ?",
                (approval_id,),
            ).fetchone()
            return self._from_row(row) if row else None

    async def list_for_run(self, run_id: str) -> list[Approval]:
        async with self._lock:
            return await asyncio.to_thread(self._list_for_run_sync, run_id)

    def _list_for_run_sync(self, run_id: str) -> list[Approval]:
        with self._open() as conn:
            rows = conn.execute(
                "SELECT * FROM approvals WHERE run_id = ? ORDER BY requested_at_ms ASC",
                (run_id,),
            ).fetchall()
            return [self._from_row(row) for row in rows]

    async def list_pending(self) -> list[Approval]:
        async with self._lock:
            return await asyncio.to_thread(self._list_pending_sync)

    def _list_pending_sync(self) -> list[Approval]:
        with self._open() as conn:
            rows = conn.execute(
                "SELECT * FROM approvals WHERE status = 'pending' ORDER BY requested_at_ms ASC"
            ).fetchall()
            return [self._from_row(row) for row in rows]

    async def resolve(self, approval_id: str, status: str, resolved_at_ms: int) -> bool:
        if status not in {"approved", "rejected"}:
            raise ValueError(f"invalid resolution status: {status}")
        async with self._lock:
            return await asyncio.to_thread(self._resolve_sync, approval_id, status, resolved_at_ms)

    def _resolve_sync(self, approval_id: str, status: str, resolved_at_ms: int) -> bool:
        with self._open() as conn:
            cur = conn.execute(
                "UPDATE approvals SET status = ?, resolved_at_ms = ? "
                "WHERE approval_id = ? AND status = 'pending'",
                (status, resolved_at_ms, approval_id),
            )
            return cur.rowcount > 0

    async def delete(self, approval_id: str) -> bool:
        async with self._lock:
            return await asyncio.to_thread(self._delete_sync, approval_id)

    def _delete_sync(self, approval_id: str) -> bool:
        with self._open() as conn:
            cur = conn.execute(
                "DELETE FROM approvals WHERE approval_id = ?",
                (approval_id,),
            )
            return cur.rowcount > 0

    @staticmethod
    def _from_row(row: sqlite3.Row) -> Approval:
        return Approval(
            approval_id=row["approval_id"],
            run_id=row["run_id"],
            gate_kind=row["gate_kind"],
            status=row["status"],
            context=json.loads(row["context_json"]) if row["context_json"] is not None else None,
            requested_at_ms=row["requested_at_ms"],
            resolved_at_ms=row["resolved_at_ms"],
        )
