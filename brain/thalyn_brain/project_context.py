"""Project-level context auto-load.

A ``THALYN.md`` (or ``CLAUDE.md``) placed at a workspace root
ships free-form context into every chat session — coding
conventions, project goals, persistent reminders the user
doesn't want to retype on every turn. Both filenames are
recognised so a project that already has a CLAUDE.md doesn't
need a duplicate.

The loader reads the first-found file and caps its length so a
massive doc can't push the user's prompt out of the context
window. The cap is intentional and conservative — projects with
larger context can still split across chunks the agent can
fetch on demand once the memory layer's recall surface lands.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

PROJECT_FILES = ("THALYN.md", "CLAUDE.md")
"""Filenames the loader probes, in priority order."""

MAX_CONTEXT_CHARS = 24_000
"""Cap on auto-loaded context — well under the smallest realistic
context window (Ollama Qwen3-Coder at 32 k tokens ≈ 96 k chars)
even after accounting for the rest of the prompt."""


@dataclass(frozen=True)
class ProjectContext:
    """The auto-loaded context bundled into the system prompt."""

    workspace_root: Path
    source_filename: str
    body: str
    truncated: bool

    def to_wire(self) -> dict[str, object]:
        return {
            "workspaceRoot": str(self.workspace_root),
            "sourceFilename": self.source_filename,
            "body": self.body,
            "truncated": self.truncated,
        }


def load_project_context(
    workspace_root: Path,
    *,
    max_chars: int = MAX_CONTEXT_CHARS,
) -> ProjectContext | None:
    """Find the first project-context file under ``workspace_root``
    and return its contents (truncated if longer than ``max_chars``).

    Returns ``None`` when no recognised file exists or
    ``workspace_root`` itself isn't a directory.
    """
    if not workspace_root.is_dir():
        return None
    for name in PROJECT_FILES:
        candidate = workspace_root / name
        if not candidate.is_file():
            continue
        try:
            text = candidate.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        truncated = len(text) > max_chars
        body = text[:max_chars] if truncated else text
        return ProjectContext(
            workspace_root=workspace_root,
            source_filename=name,
            body=body.strip(),
            truncated=truncated,
        )
    return None


def merge_into_system_prompt(
    base_system_prompt: str | None,
    context: ProjectContext | None,
) -> str | None:
    """Fold ``context`` (when present) ahead of the caller-supplied
    system prompt. Returns ``None`` when both are missing so the
    runner doesn't end up with an empty system message."""
    if context is None:
        return base_system_prompt
    header = (
        f"# Project context — {context.source_filename}\n"
        f"# (auto-loaded from {context.workspace_root})\n\n"
        f"{context.body}"
    )
    if context.truncated:
        header += "\n\n[truncated — see source file for full content]"
    if not base_system_prompt:
        return header
    return f"{header}\n\n---\n\n{base_system_prompt}"


__all__ = [
    "MAX_CONTEXT_CHARS",
    "PROJECT_FILES",
    "ProjectContext",
    "load_project_context",
    "merge_into_system_prompt",
]
