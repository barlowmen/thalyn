"""Project-context auto-load tests."""

from __future__ import annotations

from pathlib import Path

from thalyn_brain.project_context import (
    MAX_CONTEXT_CHARS,
    load_project_context,
    merge_into_system_prompt,
)


def test_returns_none_when_workspace_is_not_a_directory(tmp_path: Path) -> None:
    fake = tmp_path / "ghost"
    assert load_project_context(fake) is None


def test_returns_none_when_no_recognised_file_present(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("just a readme")
    assert load_project_context(tmp_path) is None


def test_loads_thalyn_md_when_present(tmp_path: Path) -> None:
    (tmp_path / "THALYN.md").write_text("# Project\nUse tabs.\n")
    context = load_project_context(tmp_path)
    assert context is not None
    assert context.source_filename == "THALYN.md"
    assert "Use tabs." in context.body
    assert context.truncated is False


def test_falls_through_to_claude_md_when_thalyn_absent(tmp_path: Path) -> None:
    (tmp_path / "CLAUDE.md").write_text("hello")
    context = load_project_context(tmp_path)
    assert context is not None
    assert context.source_filename == "CLAUDE.md"


def test_thalyn_md_wins_when_both_files_exist(tmp_path: Path) -> None:
    (tmp_path / "THALYN.md").write_text("thalyn content")
    (tmp_path / "CLAUDE.md").write_text("claude content")
    context = load_project_context(tmp_path)
    assert context is not None
    assert context.source_filename == "THALYN.md"
    assert "thalyn" in context.body


def test_truncates_oversized_files(tmp_path: Path) -> None:
    body = "x" * (MAX_CONTEXT_CHARS + 1000)
    (tmp_path / "THALYN.md").write_text(body)
    context = load_project_context(tmp_path)
    assert context is not None
    assert context.truncated is True
    assert len(context.body) <= MAX_CONTEXT_CHARS


def test_merge_with_no_context_returns_base(tmp_path: Path) -> None:
    assert merge_into_system_prompt("Be terse.", None) == "Be terse."
    assert merge_into_system_prompt(None, None) is None


def test_merge_prepends_context_to_base(tmp_path: Path) -> None:
    (tmp_path / "THALYN.md").write_text("Use tabs.")
    context = load_project_context(tmp_path)
    assert context is not None
    merged = merge_into_system_prompt("Be terse.", context)
    assert merged is not None
    assert merged.startswith("# Project context")
    assert "Use tabs." in merged
    assert "Be terse." in merged
    # Context comes before the base prompt with a separator.
    assert merged.index("Use tabs.") < merged.index("Be terse.")


def test_merge_marks_truncation_in_the_header(tmp_path: Path) -> None:
    body = "x" * (MAX_CONTEXT_CHARS + 5)
    (tmp_path / "THALYN.md").write_text(body)
    context = load_project_context(tmp_path)
    assert context is not None
    merged = merge_into_system_prompt(None, context)
    assert merged is not None
    assert "truncated" in merged
