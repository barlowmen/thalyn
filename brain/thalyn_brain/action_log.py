"""Cross-run action log header.

Per ``02-architecture.md`` §5, ``app.db`` carries the action_log header
row for each event (``tool_call``, ``llm_call``, ``decision``,
``file_change``, ``approval``, ``drift_check``, ``sanity_check``,
``memory_write``); the per-run audit log under ``runs/{run_id}.log``
keeps the human-readable NDJSON stream. v0.20 lands the storage with
insert + list semantics; rich query patterns (filter by kind across
projects, time-range scans for drift correlation) come later.
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

ACTION_KINDS = frozenset(
    {
        "tool_call",
        "llm_call",
        "decision",
        "file_change",
        "approval",
        "drift_check",
        "sanity_check",
        "memory_write",
    }
)


def new_action_id() -> str:
    return f"act_{uuid.uuid4().hex}"


@dataclass
class ActionLogEntry:
    action_id: str
    run_id: str
    at_ms: int
    kind: str
    payload: dict[str, Any] | None

    def to_wire(self) -> dict[str, Any]:
        return {
            "actionId": self.action_id,
            "runId": self.run_id,
            "atMs": self.at_ms,
            "kind": self.kind,
            "payload": self.payload,
        }


class ActionLogStore:
    """Append-only header store. Rows are never updated or deleted by
    the application surface; archival happens at the per-run level."""

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

    async def append(self, entry: ActionLogEntry) -> None:
        if entry.kind not in ACTION_KINDS:
            raise ValueError(f"invalid action kind: {entry.kind}")
        async with self._lock:
            await asyncio.to_thread(self._append_sync, entry)

    def _append_sync(self, entry: ActionLogEntry) -> None:
        with self._open() as conn:
            conn.execute(
                """
                INSERT INTO action_log (action_id, run_id, at_ms, kind, payload_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    entry.action_id,
                    entry.run_id,
                    entry.at_ms,
                    entry.kind,
                    json.dumps(entry.payload) if entry.payload is not None else None,
                ),
            )

    async def list_for_run(
        self,
        run_id: str,
        *,
        kind: str | None = None,
    ) -> list[ActionLogEntry]:
        async with self._lock:
            return await asyncio.to_thread(self._list_for_run_sync, run_id, kind)

    def _list_for_run_sync(
        self,
        run_id: str,
        kind: str | None,
    ) -> list[ActionLogEntry]:
        with self._open() as conn:
            if kind is not None:
                rows = conn.execute(
                    "SELECT * FROM action_log WHERE run_id = ? AND kind = ? ORDER BY at_ms ASC",
                    (run_id, kind),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM action_log WHERE run_id = ? ORDER BY at_ms ASC",
                    (run_id,),
                ).fetchall()
            return [self._from_row(row) for row in rows]

    @staticmethod
    def _from_row(row: sqlite3.Row) -> ActionLogEntry:
        return ActionLogEntry(
            action_id=row["action_id"],
            run_id=row["run_id"],
            at_ms=row["at_ms"],
            kind=row["kind"],
            payload=json.loads(row["payload_json"]) if row["payload_json"] is not None else None,
        )
