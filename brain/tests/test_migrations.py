"""Tests for the brain-side migration runner.

Per ADR-0028 the schema lives exclusively under
``thalyn_brain/migrations/`` and is applied by yoyo. These tests
cover the entry points that real callers hit:

- A fresh data directory ends up with the v1 baseline schema.
- A v1-shape database missing the late-v1 ``agent_runs`` columns
  gets backfilled on the next apply.
- Repeated calls are no-ops.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from thalyn_brain.orchestration.storage import apply_pending_migrations


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _user_tables(conn: sqlite3.Connection) -> set[str]:
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE '_yoyo_%' "
        "AND name NOT LIKE 'yoyo_%' "
        "AND name NOT LIKE 'sqlite_%'"
    )
    return {row[0] for row in cur.fetchall()}


def test_apply_to_fresh_data_dir_creates_v1_baseline(tmp_path: Path) -> None:
    apply_pending_migrations(data_dir=tmp_path)
    db_path = tmp_path / "app.db"
    assert db_path.exists()
    with sqlite3.connect(db_path) as conn:
        assert _user_tables(conn) == {
            "agent_runs",
            "schedules",
            "memory_entries",
            "mcp_connectors",
            "email_accounts",
        }
        agent_run_cols = _columns(conn, "agent_runs")
        assert {
            "sandbox_tier",
            "budget_json",
            "budget_consumed_json",
        } <= agent_run_cols


def test_backfills_late_v1_columns_on_pre_existing_db(tmp_path: Path) -> None:
    db_path = tmp_path / "app.db"
    tmp_path.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        # Simulate a v1 database created before sandbox_tier /
        # budget_json / budget_consumed_json were added.
        conn.executescript(
            """
            CREATE TABLE agent_runs (
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
            """
        )
    apply_pending_migrations(data_dir=tmp_path)
    with sqlite3.connect(db_path) as conn:
        cols = _columns(conn, "agent_runs")
        assert {"sandbox_tier", "budget_json", "budget_consumed_json"} <= cols


def test_repeated_calls_are_noops(tmp_path: Path) -> None:
    apply_pending_migrations(data_dir=tmp_path)
    apply_pending_migrations(data_dir=tmp_path)
    apply_pending_migrations(data_dir=tmp_path)
    db_path = tmp_path / "app.db"
    with sqlite3.connect(db_path) as conn:
        # Schema unchanged after repeats.
        assert "agent_runs" in _user_tables(conn)
