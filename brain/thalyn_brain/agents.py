"""Agent registry — persistent identity for brain, leads, sub-leads, workers.

The agent_records table is the centre of gravity for the v2 hierarchy
(per ADR-0021). Every long-lived agent — the brain, each project's
lead, sub-leads, and worker_persistent agents — has one row keyed by
``agent_id``. Worker runs reference their agent via ``agent_runs.agent_id``;
direct child relationships use ``agent_records.parent_agent_id``.

This v0.20 module is intentionally thin: it provides CRUD over the
table so the data-migration step and downstream IPC stubs have a
typed surface to call. Spawn / pause / resume / archive lifecycle
behaviour lands when the lead-as-first-class stage adds it (the
ADR-0021 commit ratifies the model the lifecycle work targets).
"""

from __future__ import annotations

import asyncio
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from thalyn_brain.orchestration.storage import (
    apply_pending_migrations,
    default_data_dir,
)

AGENT_KINDS = frozenset({"brain", "lead", "sub_lead", "worker_persistent"})
AGENT_STATUSES = frozenset({"active", "paused", "archived"})


def new_agent_id() -> str:
    return f"agent_{uuid.uuid4().hex}"


@dataclass
class AgentRecord:
    agent_id: str
    kind: str
    display_name: str
    parent_agent_id: str | None
    project_id: str | None
    scope_facet: str | None
    memory_namespace: str
    default_provider_id: str
    system_prompt: str
    status: str
    created_at_ms: int
    last_active_at_ms: int

    def to_wire(self) -> dict[str, Any]:
        return {
            "agentId": self.agent_id,
            "kind": self.kind,
            "displayName": self.display_name,
            "parentAgentId": self.parent_agent_id,
            "projectId": self.project_id,
            "scopeFacet": self.scope_facet,
            "memoryNamespace": self.memory_namespace,
            "defaultProviderId": self.default_provider_id,
            "systemPrompt": self.system_prompt,
            "status": self.status,
            "createdAtMs": self.created_at_ms,
            "lastActiveAtMs": self.last_active_at_ms,
        }


class AgentRecordsStore:
    """SQLite-backed agent registry sharing ``app.db`` with the v1
    stores."""

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

    async def insert(self, record: AgentRecord) -> None:
        if record.kind not in AGENT_KINDS:
            raise ValueError(f"invalid agent kind: {record.kind}")
        if record.status not in AGENT_STATUSES:
            raise ValueError(f"invalid agent status: {record.status}")
        async with self._lock:
            await asyncio.to_thread(self._insert_sync, record)

    def _insert_sync(self, record: AgentRecord) -> None:
        with self._open() as conn:
            conn.execute(
                """
                INSERT INTO agent_records
                    (agent_id, kind, display_name, parent_agent_id,
                     project_id, scope_facet, memory_namespace,
                     default_provider_id, system_prompt, status,
                     created_at_ms, last_active_at_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.agent_id,
                    record.kind,
                    record.display_name,
                    record.parent_agent_id,
                    record.project_id,
                    record.scope_facet,
                    record.memory_namespace,
                    record.default_provider_id,
                    record.system_prompt,
                    record.status,
                    record.created_at_ms,
                    record.last_active_at_ms,
                ),
            )

    async def get(self, agent_id: str) -> AgentRecord | None:
        async with self._lock:
            return await asyncio.to_thread(self._get_sync, agent_id)

    def _get_sync(self, agent_id: str) -> AgentRecord | None:
        with self._open() as conn:
            row = conn.execute(
                "SELECT * FROM agent_records WHERE agent_id = ?",
                (agent_id,),
            ).fetchone()
            return self._from_row(row) if row else None

    async def list_all(
        self,
        *,
        kind: str | None = None,
        project_id: str | None = None,
        status: str | None = None,
    ) -> list[AgentRecord]:
        async with self._lock:
            return await asyncio.to_thread(
                self._list_sync,
                kind=kind,
                project_id=project_id,
                status=status,
            )

    def _list_sync(
        self,
        *,
        kind: str | None,
        project_id: str | None,
        status: str | None,
    ) -> list[AgentRecord]:
        clauses: list[str] = []
        params: list[Any] = []
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind)
        if project_id is not None:
            clauses.append("project_id = ?")
            params.append(project_id)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._open() as conn:
            rows = conn.execute(
                f"SELECT * FROM agent_records{where} ORDER BY created_at_ms ASC",
                params,
            ).fetchall()
            return [self._from_row(row) for row in rows]

    async def update_status(self, agent_id: str, status: str, *, last_active_at_ms: int) -> bool:
        if status not in AGENT_STATUSES:
            raise ValueError(f"invalid agent status: {status}")
        async with self._lock:
            return await asyncio.to_thread(
                self._update_status_sync,
                agent_id,
                status,
                last_active_at_ms,
            )

    def _update_status_sync(self, agent_id: str, status: str, last_active_at_ms: int) -> bool:
        with self._open() as conn:
            cur = conn.execute(
                "UPDATE agent_records SET status = ?, last_active_at_ms = ? WHERE agent_id = ?",
                (status, last_active_at_ms, agent_id),
            )
            return cur.rowcount > 0

    async def delete(self, agent_id: str) -> bool:
        async with self._lock:
            return await asyncio.to_thread(self._delete_sync, agent_id)

    def _delete_sync(self, agent_id: str) -> bool:
        with self._open() as conn:
            cur = conn.execute(
                "DELETE FROM agent_records WHERE agent_id = ?",
                (agent_id,),
            )
            return cur.rowcount > 0

    @staticmethod
    def _from_row(row: sqlite3.Row) -> AgentRecord:
        return AgentRecord(
            agent_id=row["agent_id"],
            kind=row["kind"],
            display_name=row["display_name"],
            parent_agent_id=row["parent_agent_id"],
            project_id=row["project_id"],
            scope_facet=row["scope_facet"],
            memory_namespace=row["memory_namespace"],
            default_provider_id=row["default_provider_id"],
            system_prompt=row["system_prompt"],
            status=row["status"],
            created_at_ms=row["created_at_ms"],
            last_active_at_ms=row["last_active_at_ms"],
        )
