"""Eternal thread + rolling summarizer storage.

Three tightly coupled tables live here: ``threads`` (the eternal
container, one per user in v1), ``thread_turns`` (every utterance with
provenance and confidence), and ``session_digests`` (rolling summaries
the summarizer node writes at session boundaries). Per ADR-0028 the
brain owns these stores; per ``02-architecture.md`` §9 the durability
budget treats them with the same care as per-run checkpoints.

This v0.20 module is intentionally thin — CRUD over each table only.
Episodic indexing, the summarizer node, and recall logic land in the
eternal-thread durability stage.
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

THREAD_TURN_ROLES = frozenset({"user", "brain", "lead", "system"})


def new_thread_id() -> str:
    return f"thread_{uuid.uuid4().hex}"


def new_turn_id() -> str:
    return f"turn_{uuid.uuid4().hex}"


def new_digest_id() -> str:
    return f"digest_{uuid.uuid4().hex}"


@dataclass
class Thread:
    thread_id: str
    user_scope: str
    created_at_ms: int
    last_active_at_ms: int

    def to_wire(self) -> dict[str, Any]:
        return {
            "threadId": self.thread_id,
            "userScope": self.user_scope,
            "createdAtMs": self.created_at_ms,
            "lastActiveAtMs": self.last_active_at_ms,
        }


@dataclass
class ThreadTurn:
    turn_id: str
    thread_id: str
    project_id: str | None
    agent_id: str | None
    role: str
    body: str
    provenance: dict[str, Any] | None
    confidence: dict[str, Any] | None
    episodic_index_ptr: dict[str, Any] | None
    at_ms: int

    def to_wire(self) -> dict[str, Any]:
        return {
            "turnId": self.turn_id,
            "threadId": self.thread_id,
            "projectId": self.project_id,
            "agentId": self.agent_id,
            "role": self.role,
            "body": self.body,
            "provenance": self.provenance,
            "confidence": self.confidence,
            "episodicIndexPtr": self.episodic_index_ptr,
            "atMs": self.at_ms,
        }


@dataclass
class SessionDigest:
    digest_id: str
    thread_id: str
    window_start_ms: int
    window_end_ms: int
    structured_summary: dict[str, Any]
    second_level_summary_of: str | None

    def to_wire(self) -> dict[str, Any]:
        return {
            "digestId": self.digest_id,
            "threadId": self.thread_id,
            "windowStartMs": self.window_start_ms,
            "windowEndMs": self.window_end_ms,
            "structuredSummary": self.structured_summary,
            "secondLevelSummaryOf": self.second_level_summary_of,
        }


class ThreadsStore:
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

    # ----- threads -------------------------------------------------- #

    async def insert_thread(self, thread: Thread) -> None:
        async with self._lock:
            await asyncio.to_thread(self._insert_thread_sync, thread)

    def _insert_thread_sync(self, thread: Thread) -> None:
        with self._open() as conn:
            conn.execute(
                """
                INSERT INTO threads
                    (thread_id, user_scope, created_at_ms, last_active_at_ms)
                VALUES (?, ?, ?, ?)
                """,
                (
                    thread.thread_id,
                    thread.user_scope,
                    thread.created_at_ms,
                    thread.last_active_at_ms,
                ),
            )

    async def get_thread(self, thread_id: str) -> Thread | None:
        async with self._lock:
            return await asyncio.to_thread(self._get_thread_sync, thread_id)

    def _get_thread_sync(self, thread_id: str) -> Thread | None:
        with self._open() as conn:
            row = conn.execute(
                "SELECT * FROM threads WHERE thread_id = ?",
                (thread_id,),
            ).fetchone()
            return self._thread_from_row(row) if row else None

    async def list_threads(self) -> list[Thread]:
        async with self._lock:
            return await asyncio.to_thread(self._list_threads_sync)

    def _list_threads_sync(self) -> list[Thread]:
        with self._open() as conn:
            rows = conn.execute("SELECT * FROM threads ORDER BY created_at_ms ASC").fetchall()
            return [self._thread_from_row(row) for row in rows]

    async def touch_thread(self, thread_id: str, last_active_at_ms: int) -> bool:
        async with self._lock:
            return await asyncio.to_thread(self._touch_thread_sync, thread_id, last_active_at_ms)

    def _touch_thread_sync(self, thread_id: str, last_active_at_ms: int) -> bool:
        with self._open() as conn:
            cur = conn.execute(
                "UPDATE threads SET last_active_at_ms = ? WHERE thread_id = ?",
                (last_active_at_ms, thread_id),
            )
            return cur.rowcount > 0

    async def delete_thread(self, thread_id: str) -> bool:
        async with self._lock:
            return await asyncio.to_thread(self._delete_thread_sync, thread_id)

    def _delete_thread_sync(self, thread_id: str) -> bool:
        with self._open() as conn:
            cur = conn.execute(
                "DELETE FROM threads WHERE thread_id = ?",
                (thread_id,),
            )
            return cur.rowcount > 0

    # ----- thread_turns --------------------------------------------- #

    async def insert_turn(self, turn: ThreadTurn) -> None:
        if turn.role not in THREAD_TURN_ROLES:
            raise ValueError(f"invalid thread-turn role: {turn.role}")
        async with self._lock:
            await asyncio.to_thread(self._insert_turn_sync, turn)

    def _insert_turn_sync(self, turn: ThreadTurn) -> None:
        with self._open() as conn:
            conn.execute(
                """
                INSERT INTO thread_turns
                    (turn_id, thread_id, project_id, agent_id, role, body,
                     provenance_json, confidence_json,
                     episodic_index_ptr_json, at_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    turn.turn_id,
                    turn.thread_id,
                    turn.project_id,
                    turn.agent_id,
                    turn.role,
                    turn.body,
                    json.dumps(turn.provenance) if turn.provenance is not None else None,
                    json.dumps(turn.confidence) if turn.confidence is not None else None,
                    json.dumps(turn.episodic_index_ptr)
                    if turn.episodic_index_ptr is not None
                    else None,
                    turn.at_ms,
                ),
            )

    async def list_turns(
        self,
        thread_id: str,
        *,
        limit: int | None = None,
    ) -> list[ThreadTurn]:
        async with self._lock:
            return await asyncio.to_thread(self._list_turns_sync, thread_id, limit)

    def _list_turns_sync(self, thread_id: str, limit: int | None) -> list[ThreadTurn]:
        with self._open() as conn:
            if limit is not None:
                rows = conn.execute(
                    "SELECT * FROM thread_turns WHERE thread_id = ? ORDER BY at_ms DESC LIMIT ?",
                    (thread_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM thread_turns WHERE thread_id = ? ORDER BY at_ms ASC",
                    (thread_id,),
                ).fetchall()
            return [self._turn_from_row(row) for row in rows]

    async def get_turn(self, turn_id: str) -> ThreadTurn | None:
        async with self._lock:
            return await asyncio.to_thread(self._get_turn_sync, turn_id)

    def _get_turn_sync(self, turn_id: str) -> ThreadTurn | None:
        with self._open() as conn:
            row = conn.execute(
                "SELECT * FROM thread_turns WHERE turn_id = ?",
                (turn_id,),
            ).fetchone()
            return self._turn_from_row(row) if row else None

    # ----- session_digests ------------------------------------------ #

    async def insert_digest(self, digest: SessionDigest) -> None:
        async with self._lock:
            await asyncio.to_thread(self._insert_digest_sync, digest)

    def _insert_digest_sync(self, digest: SessionDigest) -> None:
        with self._open() as conn:
            conn.execute(
                """
                INSERT INTO session_digests
                    (digest_id, thread_id, window_start_ms, window_end_ms,
                     structured_summary_json, second_level_summary_of)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    digest.digest_id,
                    digest.thread_id,
                    digest.window_start_ms,
                    digest.window_end_ms,
                    json.dumps(digest.structured_summary),
                    digest.second_level_summary_of,
                ),
            )

    async def list_digests(self, thread_id: str) -> list[SessionDigest]:
        async with self._lock:
            return await asyncio.to_thread(self._list_digests_sync, thread_id)

    def _list_digests_sync(self, thread_id: str) -> list[SessionDigest]:
        with self._open() as conn:
            rows = conn.execute(
                "SELECT * FROM session_digests WHERE thread_id = ? ORDER BY window_end_ms ASC",
                (thread_id,),
            ).fetchall()
            return [self._digest_from_row(row) for row in rows]

    # ----- row mappers ---------------------------------------------- #

    @staticmethod
    def _thread_from_row(row: sqlite3.Row) -> Thread:
        return Thread(
            thread_id=row["thread_id"],
            user_scope=row["user_scope"],
            created_at_ms=row["created_at_ms"],
            last_active_at_ms=row["last_active_at_ms"],
        )

    @staticmethod
    def _turn_from_row(row: sqlite3.Row) -> ThreadTurn:
        return ThreadTurn(
            turn_id=row["turn_id"],
            thread_id=row["thread_id"],
            project_id=row["project_id"],
            agent_id=row["agent_id"],
            role=row["role"],
            body=row["body"],
            provenance=json.loads(row["provenance_json"])
            if row["provenance_json"] is not None
            else None,
            confidence=json.loads(row["confidence_json"])
            if row["confidence_json"] is not None
            else None,
            episodic_index_ptr=json.loads(row["episodic_index_ptr_json"])
            if row["episodic_index_ptr_json"] is not None
            else None,
            at_ms=row["at_ms"],
        )

    @staticmethod
    def _digest_from_row(row: sqlite3.Row) -> SessionDigest:
        return SessionDigest(
            digest_id=row["digest_id"],
            thread_id=row["thread_id"],
            window_start_ms=row["window_start_ms"],
            window_end_ms=row["window_end_ms"],
            structured_summary=json.loads(row["structured_summary_json"]),
            second_level_summary_of=row["second_level_summary_of"],
        )
