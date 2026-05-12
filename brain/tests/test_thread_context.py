"""Per-turn context assembly tests.

The assembler is the boundary the eternal thread folds into the
runner's prompt — its rendering decisions drive what every turn
sees. These tests cover the personal-memory recall path that lights
up F6.4/F6.5: a token referenced in the current turn that didn't
appear recently fans out to ``personal``-scope memory and the
matching entries land in the assembled system prompt.
"""

from __future__ import annotations

import time
from pathlib import Path

from thalyn_brain.action_registry import ActionSummary
from thalyn_brain.memory import MemoryEntry, MemoryStore, new_memory_id
from thalyn_brain.thread_context import assemble_context
from thalyn_brain.threads import (
    Thread,
    ThreadsStore,
    ThreadTurn,
    new_thread_id,
    new_turn_id,
)


def _now_ms() -> int:
    return int(time.time() * 1000)


async def _seed_thread(store: ThreadsStore) -> Thread:
    thread = Thread(
        thread_id=new_thread_id(),
        user_scope="self",
        created_at_ms=_now_ms(),
        last_active_at_ms=_now_ms(),
    )
    await store.insert_thread(thread)
    return thread


async def _seed_personal_memory(store: MemoryStore, body: str) -> MemoryEntry:
    now = _now_ms()
    entry = MemoryEntry(
        memory_id=new_memory_id(),
        project_id=None,
        scope="personal",
        kind="preference",
        body=body,
        author="user",
        created_at_ms=now,
        updated_at_ms=now,
    )
    await store.insert(entry)
    return entry


async def test_personal_memory_surfaces_when_token_misses_recent(tmp_path: Path) -> None:
    """Implicit recall: the current turn references a topic that
    isn't in the recent window, so the assembler pulls the matching
    personal-memory entry into the system prompt."""
    threads = ThreadsStore(data_dir=tmp_path)
    memory = MemoryStore(data_dir=tmp_path)
    thread = await _seed_thread(threads)
    await _seed_personal_memory(
        memory,
        "User does not auto-merge pull requests; always wait for human review.",
    )

    assembled = await assemble_context(
        threads,
        thread_id=thread.thread_id,
        user_message="Should I configure auto-merge for renovate PRs?",
        memory_store=memory,
    )

    assert any("auto-merge" in entry.body for entry in assembled.personal_memory_hits)
    assert "# Personal memory references" in assembled.system_prompt
    assert "auto-merge" in assembled.system_prompt


async def test_personal_memory_skipped_when_no_distinctive_tokens(tmp_path: Path) -> None:
    """A turn whose tokens all live in the recent window doesn't
    earn a memory round-trip — the heuristic is the same one that
    gates eternal-transcript recall."""
    threads = ThreadsStore(data_dir=tmp_path)
    memory = MemoryStore(data_dir=tmp_path)
    thread = await _seed_thread(threads)
    base = _now_ms()
    await threads.insert_turn(
        ThreadTurn(
            turn_id=new_turn_id(),
            thread_id=thread.thread_id,
            project_id=None,
            agent_id=None,
            role="user",
            body="Discussing renovate updates this week.",
            provenance=None,
            confidence=None,
            episodic_index_ptr=None,
            at_ms=base,
            status="completed",
        )
    )
    await _seed_personal_memory(memory, "Tabs over spaces.")

    assembled = await assemble_context(
        threads,
        thread_id=thread.thread_id,
        user_message="renovate updates",
        memory_store=memory,
    )

    assert assembled.personal_memory_hits == []
    assert "# Personal memory references" not in assembled.system_prompt


async def test_personal_memory_ignored_without_store(tmp_path: Path) -> None:
    """``memory_store=None`` keeps the legacy assembler path quiet —
    no personal-memory section, no errors."""
    threads = ThreadsStore(data_dir=tmp_path)
    thread = await _seed_thread(threads)

    assembled = await assemble_context(
        threads,
        thread_id=thread.thread_id,
        user_message="auto-merge configuration question",
    )

    assert assembled.personal_memory_hits == []
    assert "# Personal memory references" not in assembled.system_prompt


async def test_personal_memory_does_not_pull_other_scopes(tmp_path: Path) -> None:
    """Project / agent rows must not surface in the personal-memory
    section — the F6.4/F6.5 contract is user-scope cross-project."""
    threads = ThreadsStore(data_dir=tmp_path)
    memory = MemoryStore(data_dir=tmp_path)
    thread = await _seed_thread(threads)
    now = _now_ms()
    await memory.insert(
        MemoryEntry(
            memory_id=new_memory_id(),
            project_id="proj_default",
            scope="project",
            kind="reference",
            body="auto-merge enabled for the docs repo only.",
            author="user",
            created_at_ms=now,
            updated_at_ms=now,
        )
    )
    await memory.insert(
        MemoryEntry(
            memory_id=new_memory_id(),
            project_id=None,
            agent_id="agent_brain",
            scope="agent",
            kind="reference",
            body="auto-merge note from a worker run.",
            author="agent",
            created_at_ms=now,
            updated_at_ms=now,
        )
    )

    assembled = await assemble_context(
        threads,
        thread_id=thread.thread_id,
        user_message="Should auto-merge be on for the renovate cron?",
        memory_store=memory,
    )

    assert assembled.personal_memory_hits == []


async def test_personal_memory_hit_count_capped(tmp_path: Path) -> None:
    """A flood of matching personal entries shouldn't blow the
    prompt out — the assembler caps the merged result."""
    threads = ThreadsStore(data_dir=tmp_path)
    memory = MemoryStore(data_dir=tmp_path)
    thread = await _seed_thread(threads)
    for i in range(8):
        await _seed_personal_memory(
            memory,
            f"renovate-policy preference number {i}",
        )

    assembled = await assemble_context(
        threads,
        thread_id=thread.thread_id,
        user_message="discuss renovate-policy preferences",
        memory_store=memory,
        personal_memory_limit=3,
    )

    assert len(assembled.personal_memory_hits) <= 3


async def test_action_summaries_render_into_system_prompt(tmp_path: Path) -> None:
    """When the action registry is wired, the assembler folds the
    lean (name + description) summary list into the system prompt so
    the LLM knows the conversational actions exist without paying
    the schema cost on every turn."""
    threads = ThreadsStore(data_dir=tmp_path)
    thread = await _seed_thread(threads)

    summaries = [
        ActionSummary(
            name="routing.set_override",
            description="Route a task tag to a specific provider in the current project.",
            hard_gate=False,
        ),
        ActionSummary(
            name="email.send",
            description="Send an email on the user's behalf.",
            hard_gate=True,
        ),
    ]
    assembled = await assemble_context(
        threads,
        thread_id=thread.thread_id,
        user_message="hello",
        base_system_prompt="You are Thalyn.",
        action_summaries=summaries,
    )

    assert "# Conversational actions available" in assembled.system_prompt
    assert "``routing.set_override``" in assembled.system_prompt
    assert "[hard-gated]" in assembled.system_prompt
    # Hard-gated marker rides next to ``email.send`` specifically.
    assert "``email.send`` [hard-gated]" in assembled.system_prompt


async def test_action_summaries_block_omitted_when_registry_not_wired(tmp_path: Path) -> None:
    """A fresh thread with no action registry must not render an
    empty 'available actions' header."""
    threads = ThreadsStore(data_dir=tmp_path)
    thread = await _seed_thread(threads)

    assembled = await assemble_context(
        threads,
        thread_id=thread.thread_id,
        user_message="hello",
        base_system_prompt="You are Thalyn.",
        action_summaries=None,
    )
    assert "Conversational actions available" not in assembled.system_prompt
