"""Migration 002 — backfill late-v1 columns on agent_runs.

For databases that predate the late-v1 columns (``sandbox_tier`` /
``budget_json`` / ``budget_consumed_json``), add them now so the
post-migration schema is canonical regardless of when the database
was first created. Fresh installs already have these columns from
migration 001's ``CREATE TABLE``; this migration is a no-op for them.

This logic lived in ``runs.py`` as a runtime ``ALTER TABLE`` block
before ADR-0028 moved schema ownership to yoyo.
"""

from __future__ import annotations

from typing import Any

from yoyo import step

_LATE_V1_COLUMNS: tuple[tuple[str, str], ...] = (
    ("sandbox_tier", "TEXT"),
    ("budget_json", "TEXT"),
    ("budget_consumed_json", "TEXT"),
)


def _backfill(conn: Any) -> None:
    rows = list(conn.execute("PRAGMA table_info(agent_runs)").fetchall())
    existing = {row[1] for row in rows}
    for column, column_type in _LATE_V1_COLUMNS:
        if column not in existing:
            conn.execute(f"ALTER TABLE agent_runs ADD COLUMN {column} {column_type}")


steps = [step(_backfill)]
