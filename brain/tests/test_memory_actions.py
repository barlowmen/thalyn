"""Tests for the conversational memory-write actions.

The matcher recognises "remember that …" phrasings; the executor
lands a ``personal``-scope ``preference`` row in ``MemoryStore``.
End-to-end the brain's reply is the executor's confirmation and a
subsequent ``memory.list`` shows the entry.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from thalyn_brain.action_registry import ActionRegistry
from thalyn_brain.memory import MemoryStore
from thalyn_brain.memory_actions import (
    MEMORY_REMEMBER_ACTION,
    MemoryRememberMatcher,
    register_memory_actions,
)


def test_matcher_captures_imperative_remember_body() -> None:
    matcher = MemoryRememberMatcher()
    match = matcher.try_match(
        "Thalyn, remember that I prefer atomic commits.",
        context={},
    )
    assert match is not None
    assert match.action_name == MEMORY_REMEMBER_ACTION
    assert match.inputs == {"body": "I prefer atomic commits"}


def test_matcher_supports_unaddressed_phrasing() -> None:
    matcher = MemoryRememberMatcher()
    match = matcher.try_match("remember that the build is on Monday", context={})
    assert match is not None
    assert match.inputs == {"body": "the build is on Monday"}


def test_matcher_accepts_remember_without_that() -> None:
    matcher = MemoryRememberMatcher()
    match = matcher.try_match("remember: opus is the default brain", context={})
    assert match is not None
    assert match.inputs == {"body": "opus is the default brain"}


def test_matcher_returns_none_for_non_remember_prompts() -> None:
    matcher = MemoryRememberMatcher()
    assert matcher.try_match("what's the status on the auth refactor?", context={}) is None
    # An embedded mention isn't an imperative — the matcher only
    # fires when the sentence leads with "remember".
    assert matcher.try_match("I'll try to remember later", context={}) is None


@pytest.mark.asyncio
async def test_executor_writes_personal_preference_row(tmp_path: Path) -> None:
    store = MemoryStore(data_dir=tmp_path)
    registry = ActionRegistry()
    register_memory_actions(registry, memory_store=store)

    match = registry.try_match(
        "Thalyn, remember that I prefer atomic commits.",
        context={},
    )
    assert match is not None
    result = await registry.execute(match.action_name, match.inputs)
    assert "atomic commits" in result.confirmation

    entries = await store.list_entries(scopes=["personal"])
    assert len(entries) == 1
    entry = entries[0]
    assert entry.body == "I prefer atomic commits"
    assert entry.scope == "personal"
    assert entry.kind == "preference"
    assert entry.project_id is None
    # The followup carries the memory id so the inspector / drawer
    # can deep-link to the row.
    assert result.followup is not None
    assert result.followup["memoryId"] == entry.memory_id


@pytest.mark.asyncio
async def test_executor_refuses_empty_body(tmp_path: Path) -> None:
    store = MemoryStore(data_dir=tmp_path)
    registry = ActionRegistry()
    register_memory_actions(registry, memory_store=store)
    # Direct execute (no matcher hit) with an empty body — the
    # executor surfaces a friendly refusal rather than landing an
    # empty row.
    result = await registry.execute(MEMORY_REMEMBER_ACTION, {"body": "   "})
    assert "didn't catch" in result.confirmation.lower()
    assert await store.list_entries(scopes=["personal"]) == []
