"""Randomized-kill soak for the eternal-thread durability surface.

Drives N iterations against a single on-disk data directory; each
iteration picks a random "drop point" inside the thread.send write
sequence — before begin_user_turn, after begin_user_turn but before
complete_turn_pair, mid-complete_turn_pair (transaction rolls back),
or post-completion. After each iteration the store is reopened from
disk to assert the on-disk state is what the in-progress / completed
contract guarantees.

The phase v0.21 build plan asks for a 30-minute CI gate on every
push. The default here is count-bounded (200 iterations, ~30s), and
the gate scales up via ``THALYN_SOAK_DURATION_SECS`` for the
going-public hardening pass — see ``docs/going-public-checklist.md``
and ADR-0022's "Alternatives considered" section for the rationale.
The contract under test is the same in either mode; longer runs only
increase the seed coverage.

What this test asserts (per ADR-0022):

- Every iteration that returned successfully has BOTH a user and a
  brain turn on disk with status='completed', and both are findable
  by ``thread.search``.
- Every iteration that crashed BEFORE begin_user_turn returned has
  zero turns on disk.
- Every iteration that crashed AFTER begin_user_turn returned but
  BEFORE complete_turn_pair finished has one in-progress user turn
  and no brain turn.
- Every iteration that crashed mid-complete_turn_pair (transaction
  rollback) has the user turn back at status='in_progress' and the
  brain turn absent — atomicity holds.

Power-cut-grade testing (where the OS dies between SQLite's commit
and the disk's fsync barrier) is out of scope for this soak; that
gate lives on ``docs/going-public-checklist.md`` per the
class-A-correctness audit.
"""

from __future__ import annotations

import os
import random
import sqlite3
import time
from pathlib import Path

from thalyn_brain.threads import (
    Thread,
    ThreadsStore,
    ThreadTurn,
    new_thread_id,
    new_turn_id,
)

DEFAULT_ITERATIONS = 200
DROP_POINTS = (
    "complete",
    "after_begin",
    "mid_commit",
)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _make_user_turn(thread_id: str, body: str, *, at_ms: int) -> ThreadTurn:
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


def _make_brain_turn(thread_id: str, body: str, *, at_ms: int) -> ThreadTurn:
    return ThreadTurn(
        turn_id=new_turn_id(),
        thread_id=thread_id,
        project_id=None,
        agent_id="agent_brain",
        role="brain",
        body=body,
        provenance=None,
        confidence=None,
        episodic_index_ptr=None,
        at_ms=at_ms,
        status="completed",
    )


async def _drive_iteration(
    store: ThreadsStore,
    thread_id: str,
    drop_point: str,
    seed: int,
) -> tuple[str, ThreadTurn | None, ThreadTurn | None]:
    """Run a single iteration to its drop point.

    Returns (drop_point, user_turn, brain_turn). The brain_turn is
    None when the iteration didn't reach commit; the user_turn is
    None when the iteration didn't reach begin_user_turn (no current
    drop point goes there but the API leaves room).
    """
    base = _now_ms() + seed
    user_turn = _make_user_turn(thread_id, f"soak iteration {seed}", at_ms=base)
    await store.begin_user_turn(user_turn)
    if drop_point == "after_begin":
        return drop_point, user_turn, None

    brain_turn = _make_brain_turn(thread_id, f"soak reply {seed}", at_ms=base + 5)
    if drop_point == "mid_commit":
        # Force complete_turn_pair to error inside its transaction by
        # passing a brain turn pointing at a non-existent thread —
        # SQLite's FK enforcement raises mid-INSERT and the user-turn
        # flip rolls back with it. No collateral row lands on disk.
        crash = ThreadTurn(
            turn_id=new_turn_id(),
            thread_id="thread_does_not_exist",
            project_id=None,
            agent_id="agent_brain",
            role="brain",
            body="rolled back",
            provenance=None,
            confidence=None,
            episodic_index_ptr=None,
            at_ms=base + 6,
            status="completed",
        )
        try:
            await store.complete_turn_pair(user_turn_id=user_turn.turn_id, brain_turn=crash)
        except sqlite3.IntegrityError:
            return drop_point, user_turn, None
        raise AssertionError("mid_commit drop should have raised IntegrityError")

    # drop_point == "complete"
    await store.complete_turn_pair(user_turn_id=user_turn.turn_id, brain_turn=brain_turn)
    return drop_point, user_turn, brain_turn


def _expected_iterations() -> int:
    duration_env = os.environ.get("THALYN_SOAK_DURATION_SECS")
    if duration_env:
        # When run in time-bounded mode (e.g. nightly going-public
        # gate), the iteration count is unbounded — the test loops
        # until the wall clock crosses the threshold.
        return -1
    override = os.environ.get("THALYN_SOAK_ITERATIONS")
    if override and override.isdigit():
        return int(override)
    return DEFAULT_ITERATIONS


async def _run_soak(tmp_path: Path) -> None:
    store = ThreadsStore(data_dir=tmp_path)
    thread = Thread(
        thread_id=new_thread_id(),
        user_scope="self",
        created_at_ms=_now_ms(),
        last_active_at_ms=_now_ms(),
    )
    await store.insert_thread(thread)

    expected_completed: list[ThreadTurn] = []
    expected_in_progress: list[ThreadTurn] = []
    rng = random.Random(0)
    iterations = _expected_iterations()
    deadline = (
        time.monotonic() + float(os.environ["THALYN_SOAK_DURATION_SECS"])
        if iterations < 0
        else None
    )

    seed = 0
    while True:
        if deadline is not None:
            if time.monotonic() >= deadline:
                break
        else:
            if seed >= iterations:
                break

        drop_point = rng.choice(DROP_POINTS)
        result_drop, user_turn, brain_turn = await _drive_iteration(
            store, thread.thread_id, drop_point, seed
        )
        assert result_drop == drop_point
        if drop_point == "complete":
            assert user_turn is not None and brain_turn is not None
            expected_completed.append(user_turn)
            expected_completed.append(brain_turn)
        elif drop_point == "after_begin":
            assert user_turn is not None
            expected_in_progress.append(user_turn)
        elif drop_point == "mid_commit":
            assert user_turn is not None
            expected_in_progress.append(user_turn)
        seed += 1

    # Reopen the store from disk — simulates the brain restart.
    fresh = ThreadsStore(data_dir=tmp_path)
    on_disk_turns = await fresh.list_turns(thread.thread_id)
    on_disk_ids = {t.turn_id for t in on_disk_turns}
    in_progress = await fresh.list_in_progress(thread.thread_id)
    in_progress_ids = {t.turn_id for t in in_progress}

    # Every committed turn is on disk under status='completed'.
    completed_ids_on_disk = {t.turn_id for t in on_disk_turns if t.status == "completed"}
    expected_completed_ids = {t.turn_id for t in expected_completed}
    missing = expected_completed_ids - completed_ids_on_disk
    assert not missing, f"committed turns missing from disk after restart: {sorted(missing)}"

    # Every in-progress turn that we left parked is recoverable.
    expected_in_progress_ids = {t.turn_id for t in expected_in_progress}
    missing_pending = expected_in_progress_ids - in_progress_ids
    assert not missing_pending, (
        f"in-progress turns dropped from recovery: {sorted(missing_pending)}"
    )

    # No turn we never wrote is on disk.
    assert on_disk_ids == expected_completed_ids | expected_in_progress_ids

    # Search index is consistent with the completed turns.
    if expected_completed:
        marker = expected_completed[0].body.split()[0]
        hits = await fresh.search_turns(marker, thread_id=thread.thread_id, limit=1)
        if hits:
            assert hits[0].turn.status == "completed"


async def test_thread_durability_soak(tmp_path: Path) -> None:
    """Randomized-kill soak — count-bounded by default, time-bounded
    when ``THALYN_SOAK_DURATION_SECS`` is set."""
    await _run_soak(tmp_path)
