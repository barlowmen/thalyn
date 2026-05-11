"""Unit tests for the in-memory ``PendingActionStore``.

The store is the staging area for hard-gated conversational actions:
the brain parks the parsed inputs against an approval id; the
renderer's dialog reads the entry; ``action.approve`` /
``action.reject`` resolve it. Coverage is deliberately tight — the
store has no schema, no migrations, no foreign keys; everything
lives in-process.
"""

from __future__ import annotations

import pytest
from thalyn_brain.pending_actions import (
    APPROVED,
    PENDING,
    REJECTED,
    PendingActionStore,
)


@pytest.mark.asyncio
async def test_stage_and_get_roundtrip() -> None:
    store = PendingActionStore()
    entry = await store.stage(
        action_name="email.send",
        inputs={"to": "alice@example.com", "body": "shipping update"},
        hard_gate_kind="external_send",
        preview="Email Alice",
        thread_id="thread_x",
        turn_id="turn_y",
    )
    assert entry.status == PENDING
    assert entry.approval_id.startswith("actappr_")
    fetched = await store.get(entry.approval_id)
    assert fetched is not None
    assert fetched.inputs == {"to": "alice@example.com", "body": "shipping update"}
    assert fetched.hard_gate_kind == "external_send"


@pytest.mark.asyncio
async def test_list_pending_returns_only_unresolved() -> None:
    store = PendingActionStore()
    a = await store.stage(
        action_name="a",
        inputs={},
        hard_gate_kind=None,
        preview=None,
        thread_id="t",
        turn_id="u1",
    )
    b = await store.stage(
        action_name="b",
        inputs={},
        hard_gate_kind=None,
        preview=None,
        thread_id="t",
        turn_id="u2",
    )
    await store.resolve(a.approval_id, status=APPROVED)
    pending = await store.list_pending()
    assert [e.approval_id for e in pending] == [b.approval_id]


@pytest.mark.asyncio
async def test_resolve_flips_status_and_stamps() -> None:
    store = PendingActionStore()
    entry = await store.stage(
        action_name="a",
        inputs={},
        hard_gate_kind=None,
        preview=None,
        thread_id="t",
        turn_id="u",
    )
    resolved = await store.resolve(entry.approval_id, status=APPROVED)
    assert resolved is not None
    assert resolved.status == APPROVED
    assert resolved.resolved_at_ms is not None
    # Second resolution attempt loses the race.
    assert await store.resolve(entry.approval_id, status=REJECTED) is None


@pytest.mark.asyncio
async def test_resolve_unknown_returns_none() -> None:
    store = PendingActionStore()
    assert await store.resolve("nope", status=APPROVED) is None


def test_resolve_rejects_invalid_status() -> None:
    store = PendingActionStore()
    import asyncio

    with pytest.raises(ValueError):
        asyncio.run(store.resolve("nope", status="pending"))
