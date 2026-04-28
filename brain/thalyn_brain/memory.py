"""Memory store — cross-session recall the agent and user share.

Each entry pairs a body of free-form text with a coarse scope (user
/ project / agent), a kind (fact / preference / reference / feedback),
an author marker so the renderer can show "who wrote this", and the
usual created/updated timestamps. Memory is the third physical SQLite
store per ``02-architecture.md`` §5.

For v0.11 the search surface is plain text matching — semantic
recall via Mem0 / LangMem stays out of scope until the dependency
choice is settled (the post-v0.6 architecture review flagged
LangMem as the LangGraph-native option to evaluate). The shape of
the API is the same either way; the v0.11 SQLite implementation
is the thing the renderer + brain are built against today.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
import uuid
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from thalyn_brain.orchestration.storage import (
    apply_pending_migrations,
    default_data_dir,
)

MEMORY_SCOPES = frozenset({"user", "project", "agent"})
MEMORY_KINDS = frozenset({"fact", "preference", "reference", "feedback"})


@dataclass
class MemoryEntry:
    """One memory row exposed over JSON-RPC."""

    memory_id: str
    project_id: str | None
    scope: str
    kind: str
    body: str
    author: str
    created_at_ms: int
    updated_at_ms: int

    def to_wire(self) -> dict[str, Any]:
        d = asdict(self)
        return {
            "memoryId": d["memory_id"],
            "projectId": d["project_id"],
            "scope": d["scope"],
            "kind": d["kind"],
            "body": d["body"],
            "author": d["author"],
            "createdAtMs": d["created_at_ms"],
            "updatedAtMs": d["updated_at_ms"],
        }


@dataclass
class MemoryUpdate:
    """Fields the update path accepts."""

    body: str | None = None
    kind: str | None = None
    scope: str | None = None
    explicit_keys: set[str] = field(default_factory=set, init=False)

    def with_body(self, body: str) -> MemoryUpdate:
        self.body = body
        self.explicit_keys.add("body")
        return self

    def with_kind(self, kind: str) -> MemoryUpdate:
        self.kind = kind
        self.explicit_keys.add("kind")
        return self

    def with_scope(self, scope: str) -> MemoryUpdate:
        self.scope = scope
        self.explicit_keys.add("scope")
        return self


class MemoryStore:
    """SQLite-backed memory index sharing ``app.db`` with the runs +
    schedules tables."""

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

    async def insert(self, entry: MemoryEntry) -> None:
        async with self._lock:
            await asyncio.to_thread(self._insert_sync, entry)

    def _insert_sync(self, entry: MemoryEntry) -> None:
        if entry.scope not in MEMORY_SCOPES:
            raise ValueError(f"invalid scope: {entry.scope}")
        if entry.kind not in MEMORY_KINDS:
            raise ValueError(f"invalid kind: {entry.kind}")
        with self._open() as conn:
            conn.execute(
                """
                INSERT INTO memory_entries (
                    memory_id, project_id, scope, kind, body, author,
                    created_at_ms, updated_at_ms, embedding_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    entry.memory_id,
                    entry.project_id,
                    entry.scope,
                    entry.kind,
                    entry.body,
                    entry.author,
                    entry.created_at_ms,
                    entry.updated_at_ms,
                ),
            )

    async def get(self, memory_id: str) -> MemoryEntry | None:
        async with self._lock:
            return await asyncio.to_thread(self._get_sync, memory_id)

    def _get_sync(self, memory_id: str) -> MemoryEntry | None:
        with self._open() as conn:
            row = conn.execute(
                "SELECT * FROM memory_entries WHERE memory_id = ?",
                (memory_id,),
            ).fetchone()
        return _row_to_entry(row) if row else None

    async def list_entries(
        self,
        *,
        project_id: str | None = None,
        scopes: Iterable[str] | None = None,
        limit: int = 200,
    ) -> list[MemoryEntry]:
        async with self._lock:
            return await asyncio.to_thread(
                self._list_sync,
                project_id,
                scopes,
                limit,
            )

    def _list_sync(
        self,
        project_id: str | None,
        scopes: Iterable[str] | None,
        limit: int,
    ) -> list[MemoryEntry]:
        clauses: list[str] = []
        values: list[Any] = []
        if project_id is not None:
            clauses.append("(project_id = ? OR project_id IS NULL)")
            values.append(project_id)
        if scopes is not None:
            scope_list = list(scopes)
            if scope_list:
                clauses.append(f"scope IN ({','.join('?' * len(scope_list))})")
                values.extend(scope_list)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        values.append(limit)
        with self._open() as conn:
            rows = conn.execute(
                f"SELECT * FROM memory_entries{where} ORDER BY created_at_ms DESC LIMIT ?",
                values,
            ).fetchall()
        return [_row_to_entry(row) for row in rows]

    async def search(
        self,
        query: str,
        *,
        project_id: str | None = None,
        limit: int = 20,
    ) -> list[MemoryEntry]:
        """Plain-text LIKE search over the body. v0.11 MVP — a
        semantic-recall replacement is the next refinement."""
        async with self._lock:
            return await asyncio.to_thread(self._search_sync, query, project_id, limit)

    def _search_sync(
        self,
        query: str,
        project_id: str | None,
        limit: int,
    ) -> list[MemoryEntry]:
        if not query.strip():
            return []
        like = f"%{query.strip()}%"
        clauses = ["body LIKE ?"]
        values: list[Any] = [like]
        if project_id is not None:
            clauses.append("(project_id = ? OR project_id IS NULL)")
            values.append(project_id)
        values.append(limit)
        with self._open() as conn:
            rows = conn.execute(
                f"SELECT * FROM memory_entries WHERE {' AND '.join(clauses)} "
                "ORDER BY created_at_ms DESC LIMIT ?",
                values,
            ).fetchall()
        return [_row_to_entry(row) for row in rows]

    async def update(self, memory_id: str, update: MemoryUpdate) -> bool:
        async with self._lock:
            return await asyncio.to_thread(self._update_sync, memory_id, update)

    def _update_sync(self, memory_id: str, update: MemoryUpdate) -> bool:
        sets: list[str] = []
        values: list[Any] = []
        if "body" in update.explicit_keys:
            sets.append("body = ?")
            values.append(update.body or "")
        if "kind" in update.explicit_keys:
            if update.kind not in MEMORY_KINDS:
                raise ValueError(f"invalid kind: {update.kind}")
            sets.append("kind = ?")
            values.append(update.kind)
        if "scope" in update.explicit_keys:
            if update.scope not in MEMORY_SCOPES:
                raise ValueError(f"invalid scope: {update.scope}")
            sets.append("scope = ?")
            values.append(update.scope)
        if not sets:
            return False
        sets.append("updated_at_ms = ?")
        values.append(int(time.time() * 1000))
        values.append(memory_id)
        with self._open() as conn:
            cursor = conn.execute(
                f"UPDATE memory_entries SET {', '.join(sets)} WHERE memory_id = ?",
                values,
            )
        return cursor.rowcount > 0

    async def delete(self, memory_id: str) -> bool:
        async with self._lock:
            return await asyncio.to_thread(self._delete_sync, memory_id)

    def _delete_sync(self, memory_id: str) -> bool:
        with self._open() as conn:
            cursor = conn.execute(
                "DELETE FROM memory_entries WHERE memory_id = ?",
                (memory_id,),
            )
        return cursor.rowcount > 0


def _row_to_entry(row: sqlite3.Row) -> MemoryEntry:
    return MemoryEntry(
        memory_id=row["memory_id"],
        project_id=row["project_id"],
        scope=row["scope"],
        kind=row["kind"],
        body=row["body"],
        author=row["author"],
        created_at_ms=row["created_at_ms"],
        updated_at_ms=row["updated_at_ms"],
    )


def new_memory_id() -> str:
    return f"m_{int(time.time())}_{uuid.uuid4().hex[:8]}"


__all__ = [
    "MEMORY_KINDS",
    "MEMORY_SCOPES",
    "MemoryEntry",
    "MemoryStore",
    "MemoryUpdate",
    "new_memory_id",
]
