"""Routing-override store — per-project worker model routing.

Per ``01-requirements.md`` §F4.6 and ``02-architecture.md`` §7.4,
worker routing maps ``(task_tag, project_id) → provider_id`` against a
per-project override table that falls back to global defaults. v0.20
lands the storage; the route-worker function and the conversational
edit path land when worker model routing ships in its own stage.

The unique constraint on ``(project_id, task_tag)`` enforces "one
override per tag per project" — set is the operation, not append.
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


def new_routing_override_id() -> str:
    return f"route_{uuid.uuid4().hex}"


@dataclass
class RoutingOverride:
    routing_override_id: str
    project_id: str
    task_tag: str
    provider_id: str
    updated_at_ms: int

    def to_wire(self) -> dict[str, Any]:
        return {
            "routingOverrideId": self.routing_override_id,
            "projectId": self.project_id,
            "taskTag": self.task_tag,
            "providerId": self.provider_id,
            "updatedAtMs": self.updated_at_ms,
        }


class RoutingOverridesStore:
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

    async def upsert(self, override: RoutingOverride) -> None:
        """Insert or replace the override for ``(project_id, task_tag)``."""
        async with self._lock:
            await asyncio.to_thread(self._upsert_sync, override)

    def _upsert_sync(self, override: RoutingOverride) -> None:
        with self._open() as conn:
            conn.execute(
                """
                INSERT INTO routing_overrides
                    (routing_override_id, project_id, task_tag, provider_id, updated_at_ms)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(project_id, task_tag) DO UPDATE SET
                    provider_id = excluded.provider_id,
                    updated_at_ms = excluded.updated_at_ms
                """,
                (
                    override.routing_override_id,
                    override.project_id,
                    override.task_tag,
                    override.provider_id,
                    override.updated_at_ms,
                ),
            )

    async def get(
        self,
        project_id: str,
        task_tag: str,
    ) -> RoutingOverride | None:
        async with self._lock:
            return await asyncio.to_thread(self._get_sync, project_id, task_tag)

    def _get_sync(self, project_id: str, task_tag: str) -> RoutingOverride | None:
        with self._open() as conn:
            row = conn.execute(
                "SELECT * FROM routing_overrides WHERE project_id = ? AND task_tag = ?",
                (project_id, task_tag),
            ).fetchone()
            return self._from_row(row) if row else None

    async def list_for_project(self, project_id: str) -> list[RoutingOverride]:
        async with self._lock:
            return await asyncio.to_thread(self._list_for_project_sync, project_id)

    def _list_for_project_sync(self, project_id: str) -> list[RoutingOverride]:
        with self._open() as conn:
            rows = conn.execute(
                "SELECT * FROM routing_overrides WHERE project_id = ? ORDER BY task_tag ASC",
                (project_id,),
            ).fetchall()
            return [self._from_row(row) for row in rows]

    async def delete(self, project_id: str, task_tag: str) -> bool:
        async with self._lock:
            return await asyncio.to_thread(self._delete_sync, project_id, task_tag)

    def _delete_sync(self, project_id: str, task_tag: str) -> bool:
        with self._open() as conn:
            cur = conn.execute(
                "DELETE FROM routing_overrides WHERE project_id = ? AND task_tag = ?",
                (project_id, task_tag),
            )
            return cur.rowcount > 0

    @staticmethod
    def _from_row(row: sqlite3.Row) -> RoutingOverride:
        return RoutingOverride(
            routing_override_id=row["routing_override_id"],
            project_id=row["project_id"],
            task_tag=row["task_tag"],
            provider_id=row["provider_id"],
            updated_at_ms=row["updated_at_ms"],
        )
