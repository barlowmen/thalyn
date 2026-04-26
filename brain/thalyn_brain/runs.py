"""Runs index — header-row store for every agent run.

Backed by a single `app.db` at the data-directory root. Per the
architecture data model in `02-architecture.md` §5 this is one of
three physical stores; v0.4 only needs the AGENT_RUN header row, so
we ship that alone. Projects, providers, schedules, and approvals
join the same database in subsequent iterations.

The eventual home for `app.db` is the Rust core (also per the
architecture); for v0.4 the brain owns it because the brain is
already running SQLite and the IPC roundtrip on every write would be
needless. When the Rust core needs cross-store queries (projects ↔
runs ↔ schedules), ownership migrates and the brain switches to
proxying through JSON-RPC.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from thalyn_brain.orchestration.state import RunStatus
from thalyn_brain.orchestration.storage import default_data_dir


@dataclass
class RunHeader:
    """The header-row shape exposed over JSON-RPC."""

    run_id: str
    project_id: str | None
    parent_run_id: str | None
    status: str
    title: str
    provider_id: str
    started_at_ms: int
    completed_at_ms: int | None
    drift_score: float
    final_response: str
    plan: dict[str, Any] | None = None

    def to_wire(self) -> dict[str, Any]:
        d = asdict(self)
        return {
            "runId": d["run_id"],
            "projectId": d["project_id"],
            "parentRunId": d["parent_run_id"],
            "status": d["status"],
            "title": d["title"],
            "providerId": d["provider_id"],
            "startedAtMs": d["started_at_ms"],
            "completedAtMs": d["completed_at_ms"],
            "driftScore": d["drift_score"],
            "finalResponse": d["final_response"],
            "plan": d["plan"],
        }


@dataclass
class RunUpdate:
    """Subset of fields that update_run accepts."""

    status: str | None = None
    completed_at_ms: int | None = None
    drift_score: float | None = None
    final_response: str | None = None
    plan: dict[str, Any] | None = None
    plan_explicit: bool = field(default=False, init=False)

    def with_plan(self, plan: dict[str, Any] | None) -> RunUpdate:
        self.plan = plan
        self.plan_explicit = True
        return self


_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_runs (
    run_id TEXT PRIMARY KEY,
    project_id TEXT,
    parent_run_id TEXT,
    status TEXT NOT NULL,
    title TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    started_at_ms INTEGER NOT NULL,
    completed_at_ms INTEGER,
    drift_score REAL NOT NULL DEFAULT 0,
    final_response TEXT NOT NULL DEFAULT '',
    plan_json TEXT
);

CREATE INDEX IF NOT EXISTS agent_runs_status_idx ON agent_runs(status);
CREATE INDEX IF NOT EXISTS agent_runs_started_idx ON agent_runs(started_at_ms);
"""


class RunsStore:
    """Synchronous-under-the-hood; async surface so callers don't
    block the event loop while SQLite does its thing."""

    def __init__(self, *, data_dir: Path | None = None) -> None:
        base = data_dir or default_data_dir()
        base.mkdir(parents=True, exist_ok=True)
        self._db_path = base / "app.db"
        self._lock = asyncio.Lock()
        with self._open() as conn:
            conn.executescript(_SCHEMA)

    def _open(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    async def insert(self, header: RunHeader) -> None:
        async with self._lock:
            await asyncio.to_thread(self._insert_sync, header)

    def _insert_sync(self, header: RunHeader) -> None:
        with self._open() as conn:
            conn.execute(
                """
                INSERT INTO agent_runs
                    (run_id, project_id, parent_run_id, status, title,
                     provider_id, started_at_ms, completed_at_ms,
                     drift_score, final_response, plan_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    header.run_id,
                    header.project_id,
                    header.parent_run_id,
                    header.status,
                    header.title,
                    header.provider_id,
                    header.started_at_ms,
                    header.completed_at_ms,
                    header.drift_score,
                    header.final_response,
                    json.dumps(header.plan) if header.plan is not None else None,
                ),
            )

    async def update(self, run_id: str, update: RunUpdate) -> None:
        async with self._lock:
            await asyncio.to_thread(self._update_sync, run_id, update)

    def _update_sync(self, run_id: str, update: RunUpdate) -> None:
        sets: list[str] = []
        values: list[Any] = []
        if update.status is not None:
            sets.append("status = ?")
            values.append(update.status)
        if update.completed_at_ms is not None:
            sets.append("completed_at_ms = ?")
            values.append(update.completed_at_ms)
        if update.drift_score is not None:
            sets.append("drift_score = ?")
            values.append(update.drift_score)
        if update.final_response is not None:
            sets.append("final_response = ?")
            values.append(update.final_response)
        if update.plan_explicit:
            sets.append("plan_json = ?")
            values.append(json.dumps(update.plan) if update.plan is not None else None)
        if not sets:
            return
        values.append(run_id)
        with self._open() as conn:
            conn.execute(
                f"UPDATE agent_runs SET {', '.join(sets)} WHERE run_id = ?",
                values,
            )

    async def list_runs(
        self,
        *,
        project_id: str | None = None,
        statuses: Iterable[str] | None = None,
        limit: int = 100,
    ) -> list[RunHeader]:
        async with self._lock:
            return await asyncio.to_thread(self._list_sync, project_id, statuses, limit)

    def _list_sync(
        self,
        project_id: str | None,
        statuses: Iterable[str] | None,
        limit: int,
    ) -> list[RunHeader]:
        clauses: list[str] = []
        values: list[Any] = []
        if project_id is not None:
            clauses.append("project_id = ?")
            values.append(project_id)
        if statuses is not None:
            status_list = list(statuses)
            if status_list:
                clauses.append(f"status IN ({','.join('?' * len(status_list))})")
                values.extend(status_list)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        values.append(limit)
        with self._open() as conn:
            rows = conn.execute(
                f"SELECT * FROM agent_runs{where} ORDER BY started_at_ms DESC LIMIT ?",
                values,
            ).fetchall()
        return [_row_to_header(row) for row in rows]

    async def get(self, run_id: str) -> RunHeader | None:
        async with self._lock:
            return await asyncio.to_thread(self._get_sync, run_id)

    def _get_sync(self, run_id: str) -> RunHeader | None:
        with self._open() as conn:
            row = conn.execute(
                "SELECT * FROM agent_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        return _row_to_header(row) if row else None

    async def list_unfinished(self) -> list[RunHeader]:
        return await self.list_runs(
            statuses=[
                RunStatus.PENDING.value,
                RunStatus.PLANNING.value,
                RunStatus.RUNNING.value,
                RunStatus.PAUSED.value,
                RunStatus.AWAITING_APPROVAL.value,
            ],
        )

    async def list_descendants(self, root_run_id: str) -> list[RunHeader]:
        """Walk the parent_run_id chain and return the full subtree.

        Includes the root row itself when one exists, so callers can
        materialise a tree without a second lookup. The order is the
        SQLite recursion order — roughly breadth-first by spawn time.
        """
        async with self._lock:
            return await asyncio.to_thread(self._list_descendants_sync, root_run_id)

    def _list_descendants_sync(self, root_run_id: str) -> list[RunHeader]:
        with self._open() as conn:
            rows = conn.execute(
                """
                WITH RECURSIVE descendants(run_id) AS (
                    SELECT run_id FROM agent_runs WHERE run_id = ?
                    UNION ALL
                    SELECT a.run_id
                    FROM agent_runs a
                    INNER JOIN descendants d ON a.parent_run_id = d.run_id
                )
                SELECT a.* FROM agent_runs a
                INNER JOIN descendants USING (run_id)
                ORDER BY a.started_at_ms ASC
                """,
                (root_run_id,),
            ).fetchall()
        return [_row_to_header(row) for row in rows]


def _row_to_header(row: sqlite3.Row) -> RunHeader:
    plan_json = row["plan_json"]
    plan = json.loads(plan_json) if plan_json else None
    return RunHeader(
        run_id=row["run_id"],
        project_id=row["project_id"],
        parent_run_id=row["parent_run_id"],
        status=row["status"],
        title=row["title"],
        provider_id=row["provider_id"],
        started_at_ms=row["started_at_ms"],
        completed_at_ms=row["completed_at_ms"],
        drift_score=row["drift_score"],
        final_response=row["final_response"],
        plan=plan,
    )
