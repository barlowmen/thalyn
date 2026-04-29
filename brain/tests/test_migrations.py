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


# ---------------------------------------------------------------------------
# v1 → v2 data fold (migration 004)
# ---------------------------------------------------------------------------


def test_v1_to_v2_seed_creates_default_entities(tmp_path: Path) -> None:
    apply_pending_migrations(data_dir=tmp_path)
    db_path = tmp_path / "app.db"
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        project = conn.execute("SELECT * FROM projects WHERE slug = 'thalyn-default'").fetchone()
        assert project is not None
        assert project["name"] == "Default"
        assert project["lead_agent_id"] == "agent_lead_default"

        brain = conn.execute(
            "SELECT * FROM agent_records WHERE agent_id = 'agent_brain'"
        ).fetchone()
        assert brain is not None
        assert brain["kind"] == "brain"
        assert brain["display_name"] == "Thalyn"

        lead = conn.execute(
            "SELECT * FROM agent_records WHERE agent_id = 'agent_lead_default'"
        ).fetchone()
        assert lead is not None
        assert lead["kind"] == "lead"
        assert lead["project_id"] == "proj_default"

        thread = conn.execute("SELECT * FROM threads WHERE thread_id = 'thread_self'").fetchone()
        assert thread is not None
        assert thread["user_scope"] == "self"


def test_v1_to_v2_seed_rekeys_existing_runs(tmp_path: Path) -> None:
    """Existing v1 agent_runs rows get parent_lead_id set to the
    default lead so the v2 hierarchy has a consistent root."""
    db_path = tmp_path / "app.db"
    tmp_path.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        # Pre-seed a v1-shaped agent_runs table with a row that has no
        # parent_lead_id (the column doesn't even exist in v0 / v1
        # databases — migrations 001-003 will add it). For this test we
        # mimic v1 by writing the v1 baseline and inserting a row first.
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
                plan_json TEXT,
                sandbox_tier TEXT,
                budget_json TEXT,
                budget_consumed_json TEXT
            );
            INSERT INTO agent_runs
                (run_id, status, title, provider_id, started_at_ms)
            VALUES
                ('run_old_1', 'completed', 'old run', 'anthropic', 0);
            """
        )
    apply_pending_migrations(data_dir=tmp_path)
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT parent_lead_id FROM agent_runs WHERE run_id = 'run_old_1'"
        ).fetchone()
        assert row is not None
        assert row[0] == "agent_lead_default"


def test_v1_to_v2_seed_is_idempotent(tmp_path: Path) -> None:
    apply_pending_migrations(data_dir=tmp_path)
    from thalyn_brain.orchestration.storage import _applied_db_paths

    _applied_db_paths.clear()
    apply_pending_migrations(data_dir=tmp_path)
    db_path = tmp_path / "app.db"
    with sqlite3.connect(db_path) as conn:
        # Exactly one default project, one brain, one default lead.
        (project_count,) = conn.execute(
            "SELECT COUNT(*) FROM projects WHERE slug = 'thalyn-default'"
        ).fetchone()
        assert project_count == 1
        (brain_count,) = conn.execute(
            "SELECT COUNT(*) FROM agent_records WHERE kind = 'brain'"
        ).fetchone()
        assert brain_count == 1
        (lead_count,) = conn.execute(
            "SELECT COUNT(*) FROM agent_records WHERE kind = 'lead' AND project_id = 'proj_default'"
        ).fetchone()
        assert lead_count == 1


# ---------------------------------------------------------------------------
# Eternal-thread durability surface (migration 005)
# ---------------------------------------------------------------------------


def test_thread_turns_status_column_present_after_apply(tmp_path: Path) -> None:
    apply_pending_migrations(data_dir=tmp_path)
    db_path = tmp_path / "app.db"
    with sqlite3.connect(db_path) as conn:
        cols = _columns(conn, "thread_turns")
        assert "status" in cols


def test_thread_turns_status_defaults_to_completed(tmp_path: Path) -> None:
    """Inserting a row without an explicit status fills 'completed' so
    historical (pre-migration) data round-trips cleanly."""
    apply_pending_migrations(data_dir=tmp_path)
    db_path = tmp_path / "app.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            "INSERT INTO thread_turns "
            "(turn_id, thread_id, role, body, at_ms) "
            "VALUES (?, 'thread_self', 'user', 'hi', 1)",
            ("turn_default_status",),
        )
        row = conn.execute(
            "SELECT status FROM thread_turns WHERE turn_id = ?",
            ("turn_default_status",),
        ).fetchone()
        assert row is not None
        assert row[0] == "completed"


def test_thread_turn_index_fts5_vtable_present(tmp_path: Path) -> None:
    apply_pending_migrations(data_dir=tmp_path)
    db_path = tmp_path / "app.db"
    with sqlite3.connect(db_path) as conn:
        # FTS5 vtables register a base table plus a few shadow tables;
        # the master entry tells us the vtable was created.
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='thread_turn_index'"
        ).fetchone()
        assert row is not None
        assert "fts5" in row[0].lower()


def test_thread_turn_index_indexes_and_searches(tmp_path: Path) -> None:
    apply_pending_migrations(data_dir=tmp_path)
    db_path = tmp_path / "app.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            "INSERT INTO thread_turn_index (turn_id, body, role) VALUES (?, ?, ?)",
            ("turn_fts_1", "the auth refactor shipped overnight", "user"),
        )
        conn.execute(
            "INSERT INTO thread_turn_index (turn_id, body, role) VALUES (?, ?, ?)",
            ("turn_fts_2", "tagged the milestone for the release", "brain"),
        )
        rows = conn.execute(
            "SELECT turn_id FROM thread_turn_index WHERE thread_turn_index MATCH ? ORDER BY rank",
            ("auth refactor",),
        ).fetchall()
        ids = [row[0] for row in rows]
        assert ids[:1] == ["turn_fts_1"]


# ---------------------------------------------------------------------------
# Five-tier memory scope rename (migration 007)
# ---------------------------------------------------------------------------


def test_legacy_user_memory_rows_renamed_to_personal(tmp_path: Path) -> None:
    """v1 stores carrying ``scope='user'`` rows must come up under
    the v2 ``personal`` tier name so the new validator doesn't trip
    on data the user already created."""
    db_path = tmp_path / "app.db"
    tmp_path.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE memory_entries (
                memory_id TEXT PRIMARY KEY,
                project_id TEXT,
                scope TEXT NOT NULL,
                kind TEXT NOT NULL,
                body TEXT NOT NULL,
                author TEXT NOT NULL,
                created_at_ms INTEGER NOT NULL,
                updated_at_ms INTEGER NOT NULL,
                embedding_json TEXT
            );
            INSERT INTO memory_entries
                (memory_id, project_id, scope, kind, body, author,
                 created_at_ms, updated_at_ms)
            VALUES
                ('m_v1_legacy', NULL, 'user', 'preference',
                 'Tabs over spaces.', 'user', 0, 0),
                ('m_v1_project', 'proj_default', 'project', 'fact',
                 'Conventional Commits.', 'user', 0, 0);
            """
        )
    apply_pending_migrations(data_dir=tmp_path)
    with sqlite3.connect(db_path) as conn:
        scopes = dict(conn.execute("SELECT memory_id, scope FROM memory_entries").fetchall())
        assert scopes["m_v1_legacy"] == "personal"
        assert scopes["m_v1_project"] == "project"


def test_migration_007_is_idempotent(tmp_path: Path) -> None:
    apply_pending_migrations(data_dir=tmp_path)
    from thalyn_brain.orchestration.storage import _applied_db_paths

    _applied_db_paths.clear()
    apply_pending_migrations(data_dir=tmp_path)
    db_path = tmp_path / "app.db"
    with sqlite3.connect(db_path) as conn:
        # No legacy 'user' rows ever land — fresh installs never had any,
        # and a re-apply on a clean db is a no-op.
        (count,) = conn.execute(
            "SELECT COUNT(*) FROM memory_entries WHERE scope = 'user'"
        ).fetchone()
        assert count == 0


def test_migration_005_is_idempotent_on_existing_app_db(tmp_path: Path) -> None:
    """Re-applying after a cache flush must not re-add the status
    column or recreate the FTS5 vtable."""
    apply_pending_migrations(data_dir=tmp_path)
    from thalyn_brain.orchestration.storage import _applied_db_paths

    _applied_db_paths.clear()
    apply_pending_migrations(data_dir=tmp_path)
    db_path = tmp_path / "app.db"
    with sqlite3.connect(db_path) as conn:
        cols = _columns(conn, "thread_turns")
        # `status` exists exactly once (no duplicate ALTER errors).
        assert len([c for c in cols if c == "status"]) == 1
