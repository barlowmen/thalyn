"""Eternal thread + rolling summarizer storage.

Three tightly coupled tables live here: ``threads`` (the eternal
container, one per user in v1), ``thread_turns`` (every utterance with
provenance and confidence), and ``session_digests`` (rolling summaries
the summarizer node writes at session boundaries). Per ADR-0028 the
brain owns these stores; per ``02-architecture.md`` §9 the durability
budget treats them with the same care as per-run checkpoints, and
ADR-0022 spells out the write-before-emit + fsync invariants this
module is responsible for.

Two write paths exist:

- The single-shot ``insert_turn`` for callers that already hold a
  fully-formed turn (the brain reply path emits this).
- The pair ``begin_user_turn`` / ``complete_turn_pair`` for the live
  thread.send flow: the user turn lands first with
  ``status='in_progress'`` so the recovery scan can find it after a
  power cut, then both rows commit together once the brain's reply is
  durable.

The FTS5 index is auto-maintained by the SQL triggers in migration
006 — the Python layer manages ``thread_turns.status`` and SQLite
mirrors completed rows into ``thread_turn_index`` inside the same
transaction.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from thalyn_brain.orchestration.storage import (
    apply_pending_migrations,
    default_data_dir,
)

THREAD_TURN_ROLES = frozenset({"user", "brain", "lead", "system"})
THREAD_TURN_STATUSES = frozenset({"in_progress", "completed"})


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
    status: str = "completed"

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
            "status": self.status,
        }


@dataclass
class ThreadTurnSearchHit:
    """One result row from FTS5 — the turn plus the BM25 rank."""

    turn: ThreadTurn
    rank: float
    snippet: str | None = None

    def to_wire(self) -> dict[str, Any]:
        wire = self.turn.to_wire()
        wire["rank"] = self.rank
        if self.snippet is not None:
            wire["snippet"] = self.snippet
        return wire


@dataclass
class SessionDigest:
    digest_id: str
    thread_id: str
    window_start_ms: int
    window_end_ms: int
    structured_summary: dict[str, Any]
    second_level_summary_of: str | None
    children_digest_ids: list[str] = field(default_factory=list)

    def to_wire(self) -> dict[str, Any]:
        return {
            "digestId": self.digest_id,
            "threadId": self.thread_id,
            "windowStartMs": self.window_start_ms,
            "windowEndMs": self.window_end_ms,
            "structuredSummary": self.structured_summary,
            "secondLevelSummaryOf": self.second_level_summary_of,
            "childrenDigestIds": list(self.children_digest_ids),
        }


class ThreadsStore:
    def __init__(self, *, data_dir: Path | None = None) -> None:
        base = data_dir or default_data_dir()
        apply_pending_migrations(data_dir=base)
        self._db_path = base / "app.db"
        self._lock = asyncio.Lock()

    def _open(self) -> sqlite3.Connection:
        # ADR-0022: eternal-thread tables ride at synchronous=FULL so a
        # turn the user committed survives a power cut, not just a
        # process crash. The cost is microseconds per write on healthy
        # SSDs; well inside the per-turn latency budget.
        conn = sqlite3.connect(self._db_path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = FULL")
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
        _validate_turn(turn)
        async with self._lock:
            await asyncio.to_thread(self._insert_turn_sync, turn)

    def _insert_turn_sync(self, turn: ThreadTurn) -> None:
        with self._open() as conn:
            self._exec_insert_turn(conn, turn)

    @staticmethod
    def _exec_insert_turn(conn: sqlite3.Connection, turn: ThreadTurn) -> None:
        conn.execute(
            """
            INSERT INTO thread_turns
                (turn_id, thread_id, project_id, agent_id, role, body,
                 provenance_json, confidence_json,
                 episodic_index_ptr_json, at_ms, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                turn.status,
            ),
        )

    async def begin_user_turn(self, turn: ThreadTurn) -> None:
        """Insert a user turn with ``status='in_progress'`` and fsync.

        Per ADR-0022 this lands *before* the brain emits the
        ``thread.chunk`` ``start`` notification. A crash here leaves a
        recoverable row the startup scan picks up.
        """
        if turn.role != "user":
            raise ValueError("begin_user_turn requires role='user'")
        if turn.status != "in_progress":
            raise ValueError("begin_user_turn requires status='in_progress'")
        await self.insert_turn(turn)

    async def complete_turn_pair(
        self,
        *,
        user_turn_id: str,
        brain_turn: ThreadTurn,
    ) -> None:
        """Atomically flip the user turn to completed and insert the
        brain reply.

        The two writes share one transaction: a crash inside it rolls
        both back, so search and the recent-window list never see a
        half-completed pair. The FTS triggers in migration 006 mirror
        both rows into ``thread_turn_index`` inside this transaction
        as well, so the index stays consistent at every commit point.
        """
        if brain_turn.role not in {"brain", "lead", "system"}:
            raise ValueError("complete_turn_pair: brain turn role must be brain|lead|system")
        if brain_turn.status != "completed":
            raise ValueError("complete_turn_pair: brain turn must arrive completed")
        async with self._lock:
            await asyncio.to_thread(self._complete_turn_pair_sync, user_turn_id, brain_turn)

    def _complete_turn_pair_sync(self, user_turn_id: str, brain_turn: ThreadTurn) -> None:
        with self._open() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                cur = conn.execute(
                    """
                    UPDATE thread_turns SET status = 'completed'
                    WHERE turn_id = ? AND status = 'in_progress'
                    """,
                    (user_turn_id,),
                )
                if cur.rowcount != 1:
                    raise ValueError(
                        f"complete_turn_pair: user turn {user_turn_id!r} "
                        "not found or already completed"
                    )
                self._exec_insert_turn(conn, brain_turn)
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

    async def abandon_in_progress(self, turn_id: str) -> bool:
        """Remove an in-progress turn (e.g. after a 'discard, do not
        retry' recovery decision). Returns True if a row was removed.

        The DELETE only fires when the row is still ``in_progress``,
        so a race where the row already completed never accidentally
        drops a durable turn.
        """
        async with self._lock:
            return await asyncio.to_thread(self._abandon_in_progress_sync, turn_id)

    def _abandon_in_progress_sync(self, turn_id: str) -> bool:
        with self._open() as conn:
            cur = conn.execute(
                "DELETE FROM thread_turns WHERE turn_id = ? AND status = 'in_progress'",
                (turn_id,),
            )
            return cur.rowcount > 0

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

    async def list_recent(
        self,
        thread_id: str,
        *,
        limit: int = 50,
        include_in_progress: bool = False,
    ) -> list[ThreadTurn]:
        """Return the most recent turns in chronological order.

        The eternal-chat renderer keeps this window in working memory
        per ``02-architecture.md`` §9.4. ``include_in_progress``
        defaults False because the recent-window is what the model
        sees on the next turn — feeding an unfinished turn back into
        the prompt would recurse.
        """
        if limit <= 0:
            raise ValueError("limit must be positive")
        async with self._lock:
            return await asyncio.to_thread(
                self._list_recent_sync,
                thread_id,
                limit,
                include_in_progress,
            )

    def _list_recent_sync(
        self,
        thread_id: str,
        limit: int,
        include_in_progress: bool,
    ) -> list[ThreadTurn]:
        with self._open() as conn:
            if include_in_progress:
                rows = conn.execute(
                    """
                    SELECT * FROM thread_turns
                    WHERE thread_id = ?
                    ORDER BY at_ms DESC
                    LIMIT ?
                    """,
                    (thread_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM thread_turns
                    WHERE thread_id = ? AND status = 'completed'
                    ORDER BY at_ms DESC
                    LIMIT ?
                    """,
                    (thread_id, limit),
                ).fetchall()
            ordered = list(reversed(rows))
            return [self._turn_from_row(row) for row in ordered]

    async def list_in_progress(self, thread_id: str) -> list[ThreadTurn]:
        """Return turns parked at ``status='in_progress'`` for the
        recovery prompt. Used at brain startup and exposed over IPC
        as part of the recovery flow.
        """
        async with self._lock:
            return await asyncio.to_thread(self._list_in_progress_sync, thread_id)

    def _list_in_progress_sync(self, thread_id: str) -> list[ThreadTurn]:
        with self._open() as conn:
            rows = conn.execute(
                """
                SELECT * FROM thread_turns
                WHERE thread_id = ? AND status = 'in_progress'
                ORDER BY at_ms ASC
                """,
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

    async def search_turns(
        self,
        query: str,
        *,
        thread_id: str | None = None,
        limit: int = 10,
        snippet: bool = True,
    ) -> list[ThreadTurnSearchHit]:
        """Lexical search over completed turns via FTS5.

        ``query`` is passed straight to FTS5's MATCH operator (see
        https://sqlite.org/fts5.html#full_text_query_syntax) — callers
        that take user input should sanitize / quote as appropriate.
        Results are ordered by BM25 ranking ascending (lower is more
        relevant under FTS5's convention).
        """
        if limit <= 0:
            raise ValueError("limit must be positive")
        if not query.strip():
            return []
        async with self._lock:
            return await asyncio.to_thread(
                self._search_turns_sync,
                query,
                thread_id,
                limit,
                snippet,
            )

    def _search_turns_sync(
        self,
        query: str,
        thread_id: str | None,
        limit: int,
        want_snippet: bool,
    ) -> list[ThreadTurnSearchHit]:
        with self._open() as conn:
            select_snippet = (
                ", snippet(thread_turn_index, 1, '[', ']', '…', 16)" if want_snippet else ", NULL"
            )
            if thread_id is not None:
                rows = conn.execute(
                    f"""
                    SELECT t.*, idx.rank{select_snippet}
                    FROM thread_turn_index AS idx
                    JOIN thread_turns AS t ON t.turn_id = idx.turn_id
                    WHERE thread_turn_index MATCH ?
                      AND t.thread_id = ?
                      AND t.status = 'completed'
                    ORDER BY idx.rank
                    LIMIT ?
                    """,
                    (query, thread_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    f"""
                    SELECT t.*, idx.rank{select_snippet}
                    FROM thread_turn_index AS idx
                    JOIN thread_turns AS t ON t.turn_id = idx.turn_id
                    WHERE thread_turn_index MATCH ?
                      AND t.status = 'completed'
                    ORDER BY idx.rank
                    LIMIT ?
                    """,
                    (query, limit),
                ).fetchall()
            hits: list[ThreadTurnSearchHit] = []
            for row in rows:
                turn = self._turn_from_row(row)
                rank = row["rank"]
                snippet_text = row[len(row.keys()) - 1] if want_snippet else None
                hits.append(
                    ThreadTurnSearchHit(
                        turn=turn,
                        rank=float(rank),
                        snippet=snippet_text,
                    )
                )
            return hits

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

    async def latest_digest(self, thread_id: str) -> SessionDigest | None:
        """Return the most recent digest for a thread, or None.

        Used by ``digest.latest`` and the per-turn context-assembly
        path. Second-level (parent) digests sort alongside leaves on
        ``window_end_ms`` so the latest is always the freshest summary
        regardless of compression depth.
        """
        async with self._lock:
            return await asyncio.to_thread(self._latest_digest_sync, thread_id)

    def _latest_digest_sync(self, thread_id: str) -> SessionDigest | None:
        with self._open() as conn:
            row = conn.execute(
                """
                SELECT * FROM session_digests
                WHERE thread_id = ?
                ORDER BY window_end_ms DESC, digest_id DESC
                LIMIT 1
                """,
                (thread_id,),
            ).fetchone()
            return self._digest_from_row(row) if row else None

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
        keys = set(row.keys())
        status = row["status"] if "status" in keys else "completed"
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
            status=status,
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


def _validate_turn(turn: ThreadTurn) -> None:
    if turn.role not in THREAD_TURN_ROLES:
        raise ValueError(f"invalid thread-turn role: {turn.role}")
    if turn.status not in THREAD_TURN_STATUSES:
        raise ValueError(f"invalid thread-turn status: {turn.status}")
