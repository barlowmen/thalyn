"""Schedule index — fire-on-cron triggers for stored run templates.

Each schedule pairs a cron expression (validated through
``thalyn_brain.orchestration.cron``) with a run template the brain
dispatches when the cron next fires. Schedules persist alongside
the runs index in ``app.db``.

For v0.9 the scheduler loop runs **inside the brain process** —
when Thalyn is open the schedule fires on time; when Thalyn is
closed, the schedule waits for the next start. OS-level wake (via
launchd / Task Scheduler / systemd timers) is a follow-up; the
data model and wire surface here are the same shape that
integration will plug into.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from croniter import croniter  # type: ignore[import-untyped]

from thalyn_brain.orchestration.storage import default_data_dir


@dataclass
class Schedule:
    """The persisted schedule row."""

    schedule_id: str
    project_id: str | None
    title: str
    nl_input: str
    cron: str
    run_template: dict[str, Any]
    enabled: bool
    next_run_at_ms: int | None
    last_run_at_ms: int | None
    last_run_id: str | None
    created_at_ms: int

    def to_wire(self) -> dict[str, Any]:
        d = asdict(self)
        return {
            "scheduleId": d["schedule_id"],
            "projectId": d["project_id"],
            "title": d["title"],
            "nlInput": d["nl_input"],
            "cron": d["cron"],
            "runTemplate": d["run_template"],
            "enabled": d["enabled"],
            "nextRunAtMs": d["next_run_at_ms"],
            "lastRunAtMs": d["last_run_at_ms"],
            "lastRunId": d["last_run_id"],
            "createdAtMs": d["created_at_ms"],
        }


@dataclass
class ScheduleUpdate:
    """Fields the schedules-store update path accepts."""

    enabled: bool | None = None
    next_run_at_ms: int | None = None
    last_run_at_ms: int | None = None
    last_run_id: str | None = None
    cron: str | None = None
    nl_input: str | None = None
    title: str | None = None
    run_template: dict[str, Any] | None = None
    run_template_explicit: bool = field(default=False, init=False)

    def with_run_template(self, template: dict[str, Any] | None) -> ScheduleUpdate:
        self.run_template = template
        self.run_template_explicit = True
        return self


_SCHEMA = """
CREATE TABLE IF NOT EXISTS schedules (
    schedule_id TEXT PRIMARY KEY,
    project_id TEXT,
    title TEXT NOT NULL,
    nl_input TEXT NOT NULL,
    cron TEXT NOT NULL,
    run_template_json TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    next_run_at_ms INTEGER,
    last_run_at_ms INTEGER,
    last_run_id TEXT,
    created_at_ms INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS schedules_next_idx ON schedules(next_run_at_ms);
CREATE INDEX IF NOT EXISTS schedules_enabled_idx ON schedules(enabled);
"""


class SchedulesStore:
    """SQLite-backed schedule index sharing ``app.db`` with runs."""

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

    async def insert(self, schedule: Schedule) -> None:
        async with self._lock:
            await asyncio.to_thread(self._insert_sync, schedule)

    def _insert_sync(self, schedule: Schedule) -> None:
        with self._open() as conn:
            conn.execute(
                """
                INSERT INTO schedules (
                    schedule_id, project_id, title, nl_input, cron,
                    run_template_json, enabled, next_run_at_ms,
                    last_run_at_ms, last_run_id, created_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    schedule.schedule_id,
                    schedule.project_id,
                    schedule.title,
                    schedule.nl_input,
                    schedule.cron,
                    json.dumps(schedule.run_template),
                    1 if schedule.enabled else 0,
                    schedule.next_run_at_ms,
                    schedule.last_run_at_ms,
                    schedule.last_run_id,
                    schedule.created_at_ms,
                ),
            )

    async def get(self, schedule_id: str) -> Schedule | None:
        async with self._lock:
            return await asyncio.to_thread(self._get_sync, schedule_id)

    def _get_sync(self, schedule_id: str) -> Schedule | None:
        with self._open() as conn:
            row = conn.execute(
                "SELECT * FROM schedules WHERE schedule_id = ?",
                (schedule_id,),
            ).fetchone()
        return _row_to_schedule(row) if row else None

    async def list_all(self) -> list[Schedule]:
        async with self._lock:
            return await asyncio.to_thread(self._list_sync)

    def _list_sync(self) -> list[Schedule]:
        with self._open() as conn:
            rows = conn.execute("SELECT * FROM schedules ORDER BY created_at_ms DESC").fetchall()
        return [_row_to_schedule(row) for row in rows]

    async def update(self, schedule_id: str, update: ScheduleUpdate) -> None:
        async with self._lock:
            await asyncio.to_thread(self._update_sync, schedule_id, update)

    def _update_sync(self, schedule_id: str, update: ScheduleUpdate) -> None:
        sets: list[str] = []
        values: list[Any] = []
        if update.enabled is not None:
            sets.append("enabled = ?")
            values.append(1 if update.enabled else 0)
        if update.next_run_at_ms is not None:
            sets.append("next_run_at_ms = ?")
            values.append(update.next_run_at_ms)
        if update.last_run_at_ms is not None:
            sets.append("last_run_at_ms = ?")
            values.append(update.last_run_at_ms)
        if update.last_run_id is not None:
            sets.append("last_run_id = ?")
            values.append(update.last_run_id)
        if update.cron is not None:
            sets.append("cron = ?")
            values.append(update.cron)
        if update.nl_input is not None:
            sets.append("nl_input = ?")
            values.append(update.nl_input)
        if update.title is not None:
            sets.append("title = ?")
            values.append(update.title)
        if update.run_template_explicit:
            sets.append("run_template_json = ?")
            values.append(
                json.dumps(update.run_template) if update.run_template is not None else "{}"
            )
        if not sets:
            return
        values.append(schedule_id)
        with self._open() as conn:
            conn.execute(
                f"UPDATE schedules SET {', '.join(sets)} WHERE schedule_id = ?",
                values,
            )

    async def delete(self, schedule_id: str) -> bool:
        async with self._lock:
            return await asyncio.to_thread(self._delete_sync, schedule_id)

    def _delete_sync(self, schedule_id: str) -> bool:
        with self._open() as conn:
            cursor = conn.execute(
                "DELETE FROM schedules WHERE schedule_id = ?",
                (schedule_id,),
            )
        return cursor.rowcount > 0


def _row_to_schedule(row: sqlite3.Row) -> Schedule:
    return Schedule(
        schedule_id=row["schedule_id"],
        project_id=row["project_id"],
        title=row["title"],
        nl_input=row["nl_input"],
        cron=row["cron"],
        run_template=json.loads(row["run_template_json"]),
        enabled=bool(row["enabled"]),
        next_run_at_ms=row["next_run_at_ms"],
        last_run_at_ms=row["last_run_at_ms"],
        last_run_id=row["last_run_id"],
        created_at_ms=row["created_at_ms"],
    )


def new_schedule_id() -> str:
    return f"s_{int(time.time())}_{uuid.uuid4().hex[:8]}"


def next_fire_ms(cron: str, *, now_ms: int | None = None) -> int:
    """Return the next fire time (epoch ms) for ``cron`` after ``now_ms``."""
    now_seconds = (now_ms / 1000.0) if now_ms is not None else time.time()
    iter_ = croniter(cron, now_seconds)
    fired_at_seconds: float = iter_.get_next(float)
    return int(fired_at_seconds * 1000)


# ---------------------------------------------------------------------------
# Scheduler loop
# ---------------------------------------------------------------------------


ScheduleDispatch = Callable[[Schedule], Awaitable[str | None]]
"""Callback that dispatches a schedule and returns the started run id."""


@dataclass
class SchedulerLoopConfig:
    poll_seconds: float = 10.0
    max_clock_drift_ms: int = 1000


class SchedulerLoop:
    """Polls the schedule table and dispatches schedules whose
    ``next_run_at_ms`` has elapsed.

    The loop is intentionally simple: every ``poll_seconds`` it
    rescans the table, fires anything due, and updates ``next_run_at``
    via croniter. Long sleeps are split so a freshly-created schedule
    doesn't sit unnoticed for the full interval.
    """

    def __init__(
        self,
        store: SchedulesStore,
        dispatch: ScheduleDispatch,
        *,
        config: SchedulerLoopConfig | None = None,
    ) -> None:
        self._store = store
        self._dispatch = dispatch
        self._config = config or SchedulerLoopConfig()
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        try:
            while not self._stop_event.is_set():
                await self._tick()
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=self._config.poll_seconds,
                    )
                except TimeoutError:
                    continue
        except asyncio.CancelledError:
            return

    async def _tick(self) -> None:
        async for schedule in self._due_iter():
            await self._fire(schedule)

    async def _due_iter(self) -> AsyncIterator[Schedule]:
        now_ms = int(time.time() * 1000)
        for schedule in await self._store.list_all():
            if not schedule.enabled:
                continue
            if (
                schedule.next_run_at_ms is None
                or schedule.next_run_at_ms <= now_ms + self._config.max_clock_drift_ms
            ):
                yield schedule

    async def _fire(self, schedule: Schedule) -> None:
        started_at = int(time.time() * 1000)
        try:
            run_id = await self._dispatch(schedule)
        except Exception:
            run_id = None
        next_at = next_fire_ms(schedule.cron)
        await self._store.update(
            schedule.schedule_id,
            ScheduleUpdate(
                last_run_at_ms=started_at,
                last_run_id=run_id,
                next_run_at_ms=next_at,
            ),
        )
