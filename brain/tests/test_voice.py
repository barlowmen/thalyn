"""Tests for the voice-vocabulary builder.

The Rust core wraps these terms in Whisper's ``initial_prompt``, so
correctness here means the slice carries the user's terminology
without dragging in prose noise that would push real signal off the
end of the prompt budget.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from thalyn_brain.memory import MemoryEntry, MemoryStore, new_memory_id
from thalyn_brain.projects import ProjectsStore
from thalyn_brain.voice import (
    MAX_TERMS,
    ProjectVocabulary,
    build_project_vocabulary,
)


async def _make_project(
    projects: ProjectsStore,
    *,
    name: str = "alpha",
    workspace_path: str | None = None,
) -> str:
    project = await projects.create(name=name, workspace_path=workspace_path)
    return project.project_id


def _memory(**overrides: Any) -> MemoryEntry:
    base: dict[str, Any] = {
        "memory_id": new_memory_id(),
        "project_id": None,
        "scope": "project",
        "kind": "fact",
        "body": "",
        "author": "user",
        "created_at_ms": int(time.time() * 1000),
        "updated_at_ms": int(time.time() * 1000),
    }
    base.update(overrides)
    return MemoryEntry(**base)


# ---------------------------------------------------------------------------
# Empty / missing inputs
# ---------------------------------------------------------------------------


async def test_no_project_returns_empty_terms(tmp_path: Path) -> None:
    projects = ProjectsStore(data_dir=tmp_path)
    vocab = await build_project_vocabulary(project_id=None, projects=projects)
    assert vocab == ProjectVocabulary(terms=[])


async def test_unknown_project_returns_empty_terms(tmp_path: Path) -> None:
    projects = ProjectsStore(data_dir=tmp_path)
    vocab = await build_project_vocabulary(
        project_id="proj_does_not_exist",
        projects=projects,
    )
    assert vocab == ProjectVocabulary(terms=[])


async def test_project_with_no_workspace_path_returns_empty(tmp_path: Path) -> None:
    projects = ProjectsStore(data_dir=tmp_path)
    project_id = await _make_project(projects, name="alpha", workspace_path=None)
    vocab = await build_project_vocabulary(project_id=project_id, projects=projects)
    assert vocab.terms == []


# ---------------------------------------------------------------------------
# THALYN.md identifier extraction
# ---------------------------------------------------------------------------


async def test_thalyn_md_code_spans_become_terms(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "THALYN.md").write_text(
        "# Project\n\n"
        "We use `LangGraph` and `LeadLifecycle` for the brain. The lead "
        "agent talks to `whisper-cpp-plus` directly.\n",
        encoding="utf-8",
    )
    projects = ProjectsStore(data_dir=tmp_path)
    project_id = await _make_project(projects, name="alpha", workspace_path=str(workspace))
    vocab = await build_project_vocabulary(project_id=project_id, projects=projects)
    # Code spans are highest-priority — they show up before any
    # prose-extracted identifier.
    assert "LangGraph" in vocab.terms
    assert "LeadLifecycle" in vocab.terms
    assert "whisper-cpp-plus" in vocab.terms
    code_terms = ("LangGraph", "LeadLifecycle", "whisper-cpp-plus")
    code_indices = [vocab.terms.index(t) for t in code_terms]
    assert code_indices == sorted(code_indices)


async def test_thalyn_md_headings_contribute_titles(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "THALYN.md").write_text(
        "# Lead Hierarchy\n\nSome prose.\n\n## Hard rules\n\nMore prose.\n",
        encoding="utf-8",
    )
    projects = ProjectsStore(data_dir=tmp_path)
    project_id = await _make_project(projects, name="alpha", workspace_path=str(workspace))
    vocab = await build_project_vocabulary(project_id=project_id, projects=projects)
    assert "Lead Hierarchy" in vocab.terms
    assert "Hard rules" in vocab.terms


async def test_thalyn_md_prose_pulls_camelcase_and_kebab(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "THALYN.md").write_text(
        "The team uses MyService for the auth path and the "
        "scan-leakage script during pre-commit. ALL_CAPS_CONST also "
        "appears.\n",
        encoding="utf-8",
    )
    projects = ProjectsStore(data_dir=tmp_path)
    project_id = await _make_project(projects, name="alpha", workspace_path=str(workspace))
    vocab = await build_project_vocabulary(project_id=project_id, projects=projects)
    assert "MyService" in vocab.terms
    assert "scan-leakage" in vocab.terms
    assert "ALL_CAPS_CONST" in vocab.terms


async def test_thalyn_md_dedupes_case_insensitively(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "THALYN.md").write_text(
        "`MyService` is a service. Later we mention MyService again.\n",
        encoding="utf-8",
    )
    projects = ProjectsStore(data_dir=tmp_path)
    project_id = await _make_project(projects, name="alpha", workspace_path=str(workspace))
    vocab = await build_project_vocabulary(project_id=project_id, projects=projects)
    occurrences = [t for t in vocab.terms if t.casefold() == "myservice"]
    assert occurrences == ["MyService"]


async def test_thalyn_md_drops_stopwords(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "THALYN.md").write_text(
        "`TODO`: figure out the rest. `FIXME` later. Real term: `MyKit`.\n",
        encoding="utf-8",
    )
    projects = ProjectsStore(data_dir=tmp_path)
    project_id = await _make_project(projects, name="alpha", workspace_path=str(workspace))
    vocab = await build_project_vocabulary(project_id=project_id, projects=projects)
    assert "TODO" not in vocab.terms
    assert "FIXME" not in vocab.terms
    assert "MyKit" in vocab.terms


async def test_falls_back_to_claude_md(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "CLAUDE.md").write_text(
        "We use `MarketingService` heavily.\n",
        encoding="utf-8",
    )
    projects = ProjectsStore(data_dir=tmp_path)
    project_id = await _make_project(projects, name="alpha", workspace_path=str(workspace))
    vocab = await build_project_vocabulary(project_id=project_id, projects=projects)
    assert "MarketingService" in vocab.terms


# ---------------------------------------------------------------------------
# Memory merging
# ---------------------------------------------------------------------------


async def test_memory_facts_contribute_terms(tmp_path: Path) -> None:
    projects = ProjectsStore(data_dir=tmp_path)
    project_id = await _make_project(projects, name="alpha")
    memory = MemoryStore(data_dir=tmp_path)
    await memory.insert(
        _memory(
            project_id=project_id,
            body="The lead is named Lead-Sam and owns the EmailManager surface.",
        )
    )
    vocab = await build_project_vocabulary(project_id=project_id, projects=projects, memory=memory)
    assert "Lead-Sam" in vocab.terms
    assert "EmailManager" in vocab.terms


async def test_memory_personal_tier_is_included(tmp_path: Path) -> None:
    projects = ProjectsStore(data_dir=tmp_path)
    project_id = await _make_project(projects, name="alpha")
    memory = MemoryStore(data_dir=tmp_path)
    # Personal-scope memory has no project_id binding; the builder
    # should still pull it in for cross-project vocabulary.
    await memory.insert(
        _memory(
            project_id=None,
            scope="personal",
            kind="preference",
            body="The user prefers Sonnet-4-6 for chat-style tasks.",
        )
    )
    vocab = await build_project_vocabulary(project_id=project_id, projects=projects, memory=memory)
    assert "Sonnet-4-6" in vocab.terms


async def test_thalyn_md_terms_outrank_memory_terms(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "THALYN.md").write_text(
        "We rely on `LangGraph` for the brain orchestration.\n",
        encoding="utf-8",
    )
    projects = ProjectsStore(data_dir=tmp_path)
    project_id = await _make_project(projects, name="alpha", workspace_path=str(workspace))
    memory = MemoryStore(data_dir=tmp_path)
    await memory.insert(
        _memory(
            project_id=project_id,
            body="The CapabilityRegistry feeds the renderer.",
        )
    )
    vocab = await build_project_vocabulary(project_id=project_id, projects=projects, memory=memory)
    assert vocab.terms.index("LangGraph") < vocab.terms.index("CapabilityRegistry")


# ---------------------------------------------------------------------------
# Wire shape + budget cap
# ---------------------------------------------------------------------------


async def test_wire_shape_matches_rust_parser(tmp_path: Path) -> None:
    projects = ProjectsStore(data_dir=tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "THALYN.md").write_text("`Foo` and `Bar`.", encoding="utf-8")
    project_id = await _make_project(projects, name="alpha", workspace_path=str(workspace))
    vocab = await build_project_vocabulary(project_id=project_id, projects=projects)
    wire = vocab.to_wire()
    assert set(wire.keys()) == {"terms"}
    assert wire["terms"] == ["Foo", "Bar"]


async def test_caps_terms_at_max(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    spans = " ".join(f"`Term{i}`" for i in range(MAX_TERMS * 3))
    (workspace / "THALYN.md").write_text(spans, encoding="utf-8")
    projects = ProjectsStore(data_dir=tmp_path)
    project_id = await _make_project(projects, name="alpha", workspace_path=str(workspace))
    vocab = await build_project_vocabulary(project_id=project_id, projects=projects)
    assert len(vocab.terms) == MAX_TERMS
