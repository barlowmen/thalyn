"""Tests for the --inspect-db CLI."""

from __future__ import annotations

from pathlib import Path

import pytest
from thalyn_brain.inspect_db import main as inspect_main


def test_inspect_app_dumps_v2_tables(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = inspect_main(["app", "--data-dir", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    # Migration 003 + 004 ran, so the v2 tables exist and the default
    # entities are present.
    assert "=== projects" in out
    assert "=== agent_records" in out
    assert "=== agent_runs" in out
    # The default project is seeded by migration 004.
    assert "thalyn-default" in out


def test_inspect_thread_filters_to_thread_tables(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = inspect_main(["thread", "--data-dir", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "=== threads" in out
    assert "=== thread_turns" in out
    assert "=== session_digests" in out
    # Confirm we did NOT dump app-level tables.
    assert "=== agent_runs" not in out


def test_inspect_memory_filters_to_memory_table(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = inspect_main(["memory", "--data-dir", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "=== memory_entries" in out


def test_inspect_runs_lists_agent_runs(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = inspect_main(["runs", "--data-dir", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "=== agent_runs" in out


def test_inspect_runs_with_unknown_run_id_errors(tmp_path: Path) -> None:
    rc = inspect_main(["runs", "--run-id", "nonexistent", "--data-dir", str(tmp_path)])
    assert rc == 1


def test_inspect_unknown_table_within_store_errors(tmp_path: Path) -> None:
    rc = inspect_main(["app", "--table", "not_a_real_table", "--data-dir", str(tmp_path)])
    assert rc == 2


def test_inspect_with_missing_data_dir_errors(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"
    rc = inspect_main(["app", "--data-dir", str(missing)])
    assert rc == 1
