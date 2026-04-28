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
        # v1 tables are present (the v2 tables that also land via
        # migration 003 are covered by test_v2_tables_present_after_apply).
        assert {
            "agent_runs",
            "schedules",
            "memory_entries",
            "mcp_connectors",
            "email_accounts",
        } <= _user_tables(conn)
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


# ---------------------------------------------------------------------------
# v2 schema (migration 003)
# ---------------------------------------------------------------------------


def test_v2_tables_present_after_apply(tmp_path: Path) -> None:
    apply_pending_migrations(data_dir=tmp_path)
    db_path = tmp_path / "app.db"
    with sqlite3.connect(db_path) as conn:
        tables = _user_tables(conn)
        assert {
            "threads",
            "thread_turns",
            "session_digests",
            "agent_records",
            "projects",
            "auth_backends",
            "routing_overrides",
            "approvals",
            "action_log",
        } <= tables


def test_v1_tables_extended_with_v2_columns(tmp_path: Path) -> None:
    apply_pending_migrations(data_dir=tmp_path)
    db_path = tmp_path / "app.db"
    with sqlite3.connect(db_path) as conn:
        agent_run_cols = _columns(conn, "agent_runs")
        assert {"agent_id", "parent_lead_id", "task_tags_json"} <= agent_run_cols
        memory_cols = _columns(conn, "memory_entries")
        assert {"agent_id", "provenance_json"} <= memory_cols


def test_foreign_keys_are_enforced(tmp_path: Path) -> None:
    """Inserting a thread_turn with a non-existent thread_id fails."""
    apply_pending_migrations(data_dir=tmp_path)
    db_path = tmp_path / "app.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        # Thread doesn't exist; the FK from thread_turns(thread_id) should
        # refuse this insert.
        try:
            conn.execute(
                "INSERT INTO thread_turns "
                "(turn_id, thread_id, role, body, at_ms) "
                "VALUES (?, ?, ?, ?, ?)",
                ("turn-1", "nonexistent-thread", "user", "hi", 0),
            )
            raised = False
        except sqlite3.IntegrityError:
            raised = True
        assert raised, "FK violation should have raised IntegrityError"


def test_v2_migration_is_idempotent_on_existing_app_db(tmp_path: Path) -> None:
    """Running migrations twice in a row must not error on the v2 ALTER
    statements, which are not natively idempotent in SQLite."""
    apply_pending_migrations(data_dir=tmp_path)
    # Force a fresh apply by simulating a new process: clear the cache
    # and call again. yoyo's migration log should mark 003 applied.
    from thalyn_brain.orchestration.storage import _applied_db_paths

    _applied_db_paths.clear()
    apply_pending_migrations(data_dir=tmp_path)
    db_path = tmp_path / "app.db"
    with sqlite3.connect(db_path) as conn:
        # Tables still present, columns still single (no duplicate ALTER).
        assert "thread_turns" in _user_tables(conn)
        agent_run_cols = _columns(conn, "agent_runs")
        # Each column should appear exactly once.
        assert len([c for c in agent_run_cols if c == "agent_id"]) == 1
