"""Tests for the eternal-thread durability surface (ADR-0022).

Three coupled concerns live under here:

- Migration 006's auto-sync triggers (status -> FTS index mirror).
- ``ThreadsStore``'s in-progress / completed write-pair atomicity.
- ``thread.search`` lexical recall via the FTS5 vtable.

The tests share a ``tmp_path`` so each one starts from a fresh data
directory; the migration runner is idempotent at process scope but the
file-backed state is per-test.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest
from thalyn_brain.threads import (
    SessionDigest,
    Thread,
    ThreadsStore,
    ThreadTurn,
    new_digest_id,
    new_thread_id,
    new_turn_id,
)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _seed_thread(store: ThreadsStore) -> Thread:
    """Helper: insert and return a fresh Thread row."""

    thread = Thread(
        thread_id=new_thread_id(),
        user_scope="self",
        created_at_ms=_now_ms(),
        last_active_at_ms=_now_ms(),
    )
    return thread


def _user_turn(thread_id: str, body: str, *, at_ms: int) -> ThreadTurn:
    return ThreadTurn(
        turn_id=new_turn_id(),
        thread_id=thread_id,
        project_id=None,
        agent_id=None,
        role="user",
        body=body,
        provenance=None,
        confidence=None,
        episodic_index_ptr=None,
        at_ms=at_ms,
        status="in_progress",
    )


def _brain_turn(thread_id: str, body: str, *, at_ms: int) -> ThreadTurn:
    return ThreadTurn(
        turn_id=new_turn_id(),
        thread_id=thread_id,
        project_id=None,
        agent_id="agent_brain",
        role="brain",
        body=body,
        provenance={"source": "thalyn"},
        confidence=None,
        episodic_index_ptr=None,
        at_ms=at_ms,
        status="completed",
    )


# ---------------------------------------------------------------------------
# Migration 006 — auto-sync triggers
# ---------------------------------------------------------------------------


def test_completed_insert_mirrors_into_fts_index(tmp_path: Path) -> None:
    store = ThreadsStore(data_dir=tmp_path)
    db = tmp_path / "app.db"
    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            "INSERT INTO threads (thread_id, user_scope, created_at_ms, "
            "last_active_at_ms) VALUES ('t1', 'self', 0, 0)"
        )
        conn.execute(
            "INSERT INTO thread_turns "
            "(turn_id, thread_id, role, body, at_ms, status) "
            "VALUES ('turn_completed_1', 't1', 'user', "
            "'auth refactor shipped', 1, 'completed')"
        )
        rows = conn.execute(
            "SELECT turn_id FROM thread_turn_index WHERE thread_turn_index MATCH 'auth refactor'"
        ).fetchall()
        assert rows == [("turn_completed_1",)]
    del store


def test_in_progress_insert_does_not_mirror_into_fts_index(tmp_path: Path) -> None:
    store = ThreadsStore(data_dir=tmp_path)
    db = tmp_path / "app.db"
    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            "INSERT INTO threads (thread_id, user_scope, created_at_ms, "
            "last_active_at_ms) VALUES ('t2', 'self', 0, 0)"
        )
        conn.execute(
            "INSERT INTO thread_turns "
            "(turn_id, thread_id, role, body, at_ms, status) "
            "VALUES ('turn_pending_1', 't2', 'user', "
            "'in flight content', 1, 'in_progress')"
        )
        rows = conn.execute(
            "SELECT COUNT(*) FROM thread_turn_index WHERE turn_id = 'turn_pending_1'"
        ).fetchall()
        assert rows == [(0,)]
    del store


def test_status_flip_to_completed_mirrors_into_fts_index(tmp_path: Path) -> None:
    store = ThreadsStore(data_dir=tmp_path)
    db = tmp_path / "app.db"
    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            "INSERT INTO threads (thread_id, user_scope, created_at_ms, "
            "last_active_at_ms) VALUES ('t3', 'self', 0, 0)"
        )
        conn.execute(
            "INSERT INTO thread_turns "
            "(turn_id, thread_id, role, body, at_ms, status) "
            "VALUES ('turn_flip_1', 't3', 'user', "
            "'flipped to completed', 1, 'in_progress')"
        )
        # Before the flip: no FTS row.
        before = conn.execute(
            "SELECT COUNT(*) FROM thread_turn_index WHERE turn_id = 'turn_flip_1'"
        ).fetchone()
        assert before == (0,)
        # Flip status. Trigger fires on the UPDATE.
        conn.execute("UPDATE thread_turns SET status = 'completed' WHERE turn_id = 'turn_flip_1'")
        after = conn.execute(
            "SELECT turn_id FROM thread_turn_index WHERE thread_turn_index MATCH 'flipped'"
        ).fetchall()
        assert after == [("turn_flip_1",)]
    del store


def test_delete_cascades_into_fts_index(tmp_path: Path) -> None:
    store = ThreadsStore(data_dir=tmp_path)
    db = tmp_path / "app.db"
    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            "INSERT INTO threads (thread_id, user_scope, created_at_ms, "
            "last_active_at_ms) VALUES ('t4', 'self', 0, 0)"
        )
        conn.execute(
            "INSERT INTO thread_turns "
            "(turn_id, thread_id, role, body, at_ms, status) "
            "VALUES ('turn_del_1', 't4', 'user', "
            "'about to vanish', 1, 'completed')"
        )
        conn.execute("DELETE FROM thread_turns WHERE turn_id = 'turn_del_1'")
        rows = conn.execute(
            "SELECT COUNT(*) FROM thread_turn_index WHERE turn_id = 'turn_del_1'"
        ).fetchone()
        assert rows == (0,)
    del store


# ---------------------------------------------------------------------------
# ThreadsStore — write-before-emit + atomic completion
# ---------------------------------------------------------------------------


async def test_begin_user_turn_writes_in_progress(tmp_path: Path) -> None:
    store = ThreadsStore(data_dir=tmp_path)
    thread = _seed_thread(store)
    await store.insert_thread(thread)

    user = _user_turn(thread.thread_id, "hello", at_ms=_now_ms())
    await store.begin_user_turn(user)

    fetched = await store.get_turn(user.turn_id)
    assert fetched is not None
    assert fetched.status == "in_progress"

    in_progress = await store.list_in_progress(thread.thread_id)
    assert [t.turn_id for t in in_progress] == [user.turn_id]


async def test_complete_turn_pair_flips_user_and_inserts_brain(tmp_path: Path) -> None:
    store = ThreadsStore(data_dir=tmp_path)
    thread = _seed_thread(store)
    await store.insert_thread(thread)

    base = _now_ms()
    user = _user_turn(thread.thread_id, "what's the auth status", at_ms=base)
    await store.begin_user_turn(user)

    brain = _brain_turn(thread.thread_id, "auth refactor shipped overnight", at_ms=base + 50)
    await store.complete_turn_pair(user_turn_id=user.turn_id, brain_turn=brain)

    user_after = await store.get_turn(user.turn_id)
    assert user_after is not None
    assert user_after.status == "completed"

    brain_after = await store.get_turn(brain.turn_id)
    assert brain_after is not None
    assert brain_after.status == "completed"

    in_progress = await store.list_in_progress(thread.thread_id)
    assert in_progress == []


async def test_complete_turn_pair_rejects_already_completed(tmp_path: Path) -> None:
    """A second call against the same user_turn_id must error rather
    than silently double-write the brain reply."""
    store = ThreadsStore(data_dir=tmp_path)
    thread = _seed_thread(store)
    await store.insert_thread(thread)

    user = _user_turn(thread.thread_id, "ping", at_ms=_now_ms())
    await store.begin_user_turn(user)
    brain1 = _brain_turn(thread.thread_id, "pong", at_ms=_now_ms() + 5)
    await store.complete_turn_pair(user_turn_id=user.turn_id, brain_turn=brain1)

    brain2 = _brain_turn(thread.thread_id, "duplicate pong", at_ms=_now_ms() + 10)
    with pytest.raises(ValueError):
        await store.complete_turn_pair(user_turn_id=user.turn_id, brain_turn=brain2)


async def test_complete_turn_pair_atomic_on_brain_insert_failure(tmp_path: Path) -> None:
    """If the brain insert fails (e.g. duplicate turn_id), the user
    turn must remain in_progress — the transaction rolls back together.
    """
    store = ThreadsStore(data_dir=tmp_path)
    thread = _seed_thread(store)
    await store.insert_thread(thread)

    user = _user_turn(thread.thread_id, "atomic test", at_ms=_now_ms())
    await store.begin_user_turn(user)

    duplicate = _brain_turn(thread.thread_id, "first reply", at_ms=_now_ms() + 5)
    await store.insert_turn(duplicate)  # land it stand-alone first

    # Now try to complete the pair against the same brain turn_id —
    # the INSERT in complete_turn_pair will hit the PK constraint.
    user_again = _user_turn(thread.thread_id, "atomic test 2", at_ms=_now_ms() + 10)
    await store.begin_user_turn(user_again)
    duplicate2 = ThreadTurn(
        turn_id=duplicate.turn_id,  # collision
        thread_id=thread.thread_id,
        project_id=None,
        agent_id="agent_brain",
        role="brain",
        body="conflicting body",
        provenance=None,
        confidence=None,
        episodic_index_ptr=None,
        at_ms=_now_ms() + 20,
        status="completed",
    )
    with pytest.raises(sqlite3.IntegrityError):
        await store.complete_turn_pair(user_turn_id=user_again.turn_id, brain_turn=duplicate2)

    user_after = await store.get_turn(user_again.turn_id)
    assert user_after is not None
    assert user_after.status == "in_progress"


async def test_abandon_in_progress_only_removes_pending(tmp_path: Path) -> None:
    store = ThreadsStore(data_dir=tmp_path)
    thread = _seed_thread(store)
    await store.insert_thread(thread)

    pending = _user_turn(thread.thread_id, "abandon me", at_ms=_now_ms())
    await store.begin_user_turn(pending)
    assert (await store.abandon_in_progress(pending.turn_id)) is True
    assert (await store.get_turn(pending.turn_id)) is None

    completed = ThreadTurn(
        turn_id=new_turn_id(),
        thread_id=thread.thread_id,
        project_id=None,
        agent_id=None,
        role="user",
        body="durable",
        provenance=None,
        confidence=None,
        episodic_index_ptr=None,
        at_ms=_now_ms(),
        status="completed",
    )
    await store.insert_turn(completed)
    # abandon refuses to drop a completed turn.
    assert (await store.abandon_in_progress(completed.turn_id)) is False
    assert (await store.get_turn(completed.turn_id)) is not None


# ---------------------------------------------------------------------------
# list_recent
# ---------------------------------------------------------------------------


async def test_list_recent_returns_chronological_window_excluding_pending(
    tmp_path: Path,
) -> None:
    store = ThreadsStore(data_dir=tmp_path)
    thread = _seed_thread(store)
    await store.insert_thread(thread)

    base = _now_ms()
    # 12 completed turns, ascending in time, plus one in-progress at the tail.
    for i in range(12):
        turn = ThreadTurn(
            turn_id=new_turn_id(),
            thread_id=thread.thread_id,
            project_id=None,
            agent_id=None,
            role="user" if i % 2 == 0 else "brain",
            body=f"turn {i}",
            provenance=None,
            confidence=None,
            episodic_index_ptr=None,
            at_ms=base + i,
            status="completed",
        )
        await store.insert_turn(turn)

    pending = _user_turn(thread.thread_id, "in flight", at_ms=base + 100)
    await store.begin_user_turn(pending)

    recent = await store.list_recent(thread.thread_id, limit=5)
    assert [t.body for t in recent] == [
        "turn 7",
        "turn 8",
        "turn 9",
        "turn 10",
        "turn 11",
    ]

    # When include_in_progress is on, the pending turn shows up at the tail.
    with_pending = await store.list_recent(thread.thread_id, limit=5, include_in_progress=True)
    assert with_pending[-1].turn_id == pending.turn_id


# ---------------------------------------------------------------------------
# search_turns
# ---------------------------------------------------------------------------


async def test_search_turns_returns_top_match_for_known_topic(tmp_path: Path) -> None:
    store = ThreadsStore(data_dir=tmp_path)
    thread = _seed_thread(store)
    await store.insert_thread(thread)

    fillers = [
        "discussed the q4 roadmap and milestones",
        "designed the metrics dashboard for sprint planning",
        "tagged the v0.20 release after smoke tests",
        "shipped the auth refactor overnight, lead approved",
        "talked about onboarding the new engineer this week",
    ]
    base = _now_ms() - 1_000_000
    for i, body in enumerate(fillers):
        turn = ThreadTurn(
            turn_id=new_turn_id(),
            thread_id=thread.thread_id,
            project_id=None,
            agent_id=None,
            role="user" if i % 2 == 0 else "brain",
            body=body,
            provenance=None,
            confidence=None,
            episodic_index_ptr=None,
            at_ms=base + i,
            status="completed",
        )
        await store.insert_turn(turn)

    hits = await store.search_turns("auth refactor", thread_id=thread.thread_id, limit=3)
    assert len(hits) >= 1
    assert "auth refactor" in hits[0].turn.body
    # BM25 rank is negative under FTS5 — lower numbers mean more relevant.
    assert hits[0].rank == min(h.rank for h in hits)


async def test_search_turns_rejects_in_progress_rows(tmp_path: Path) -> None:
    """Half-written turns must not surface in search."""
    store = ThreadsStore(data_dir=tmp_path)
    thread = _seed_thread(store)
    await store.insert_thread(thread)

    pending = _user_turn(thread.thread_id, "pending dragons", at_ms=_now_ms())
    await store.begin_user_turn(pending)

    hits = await store.search_turns("dragons", thread_id=thread.thread_id, limit=5)
    assert hits == []


async def test_search_turns_scopes_by_thread(tmp_path: Path) -> None:
    store = ThreadsStore(data_dir=tmp_path)
    a = _seed_thread(store)
    b = _seed_thread(store)
    await store.insert_thread(a)
    await store.insert_thread(b)

    base = _now_ms()
    for thread, body in [(a, "alpha shared phrase"), (b, "beta shared phrase")]:
        turn = ThreadTurn(
            turn_id=new_turn_id(),
            thread_id=thread.thread_id,
            project_id=None,
            agent_id=None,
            role="user",
            body=body,
            provenance=None,
            confidence=None,
            episodic_index_ptr=None,
            at_ms=base,
            status="completed",
        )
        await store.insert_turn(turn)

    a_hits = await store.search_turns("shared phrase", thread_id=a.thread_id)
    b_hits = await store.search_turns("shared phrase", thread_id=b.thread_id)
    all_hits = await store.search_turns("shared phrase")

    assert {h.turn.thread_id for h in a_hits} == {a.thread_id}
    assert {h.turn.thread_id for h in b_hits} == {b.thread_id}
    assert {h.turn.thread_id for h in all_hits} == {a.thread_id, b.thread_id}


async def test_search_turns_returns_empty_on_empty_query(tmp_path: Path) -> None:
    store = ThreadsStore(data_dir=tmp_path)
    thread = _seed_thread(store)
    await store.insert_thread(thread)
    assert await store.search_turns("   ", thread_id=thread.thread_id) == []


# ---------------------------------------------------------------------------
# Digests — latest_digest helper
# ---------------------------------------------------------------------------


async def test_latest_digest_returns_most_recent(tmp_path: Path) -> None:
    store = ThreadsStore(data_dir=tmp_path)
    thread = _seed_thread(store)
    await store.insert_thread(thread)

    older = SessionDigest(
        digest_id=new_digest_id(),
        thread_id=thread.thread_id,
        window_start_ms=1_000,
        window_end_ms=2_000,
        structured_summary={"topics": ["a"]},
        second_level_summary_of=None,
    )
    newer = SessionDigest(
        digest_id=new_digest_id(),
        thread_id=thread.thread_id,
        window_start_ms=2_000,
        window_end_ms=3_000,
        structured_summary={"topics": ["b"]},
        second_level_summary_of=None,
    )
    await store.insert_digest(older)
    await store.insert_digest(newer)

    latest = await store.latest_digest(thread.thread_id)
    assert latest is not None
    assert latest.digest_id == newer.digest_id


async def test_latest_digest_returns_none_for_empty_thread(tmp_path: Path) -> None:
    store = ThreadsStore(data_dir=tmp_path)
    thread = _seed_thread(store)
    await store.insert_thread(thread)
    assert await store.latest_digest(thread.thread_id) is None
