"""IPC tests for the eternal-thread read surface.

Covers ``thread.recent``, ``thread.search``, ``digest.latest`` —
the methods promoted from v2 stubs in this stage. ``thread.send`` and
``digest.run`` are exercised by the orchestration tests when they
land alongside the summarizer + send-path commits.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
from thalyn_brain.rpc import INVALID_PARAMS, Dispatcher
from thalyn_brain.threads import (
    SessionDigest,
    Thread,
    ThreadsStore,
    ThreadTurn,
    new_digest_id,
    new_thread_id,
    new_turn_id,
)
from thalyn_brain.threads_rpc import register_thread_methods


class _DropNotify:
    async def __call__(self, method: str, params: Any) -> None:
        return None


_drop_notify = _DropNotify()


def _now_ms() -> int:
    return int(time.time() * 1000)


def _completed_turn(thread_id: str, body: str, *, role: str = "user", at_ms: int) -> ThreadTurn:
    return ThreadTurn(
        turn_id=new_turn_id(),
        thread_id=thread_id,
        project_id=None,
        agent_id=None,
        role=role,
        body=body,
        provenance=None,
        confidence=None,
        episodic_index_ptr=None,
        at_ms=at_ms,
        status="completed",
    )


async def _build_dispatcher(tmp_path: Path) -> tuple[Dispatcher, ThreadsStore]:
    store = ThreadsStore(data_dir=tmp_path)
    dispatcher = Dispatcher()
    register_thread_methods(dispatcher, store)
    return dispatcher, store


# ---------------------------------------------------------------------------
# thread.recent
# ---------------------------------------------------------------------------


async def test_thread_recent_returns_chronological_window(tmp_path: Path) -> None:
    dispatcher, store = await _build_dispatcher(tmp_path)
    thread = Thread(
        thread_id=new_thread_id(),
        user_scope="self",
        created_at_ms=_now_ms(),
        last_active_at_ms=_now_ms(),
    )
    await store.insert_thread(thread)
    base = _now_ms()
    for i in range(5):
        await store.insert_turn(_completed_turn(thread.thread_id, f"turn {i}", at_ms=base + i))

    response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "thread.recent",
            "params": {"threadId": thread.thread_id, "limit": 3},
        },
        notify=_drop_notify,
    )
    assert response is not None
    result = response["result"]
    assert result["threadId"] == thread.thread_id
    assert [t["body"] for t in result["turns"]] == ["turn 2", "turn 3", "turn 4"]


async def test_thread_recent_rejects_missing_thread_id(tmp_path: Path) -> None:
    dispatcher, _ = await _build_dispatcher(tmp_path)
    response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "thread.recent",
            "params": {},
        },
        notify=_drop_notify,
    )
    assert response is not None
    assert response["error"]["code"] == INVALID_PARAMS


async def test_thread_recent_rejects_oversized_limit(tmp_path: Path) -> None:
    dispatcher, _ = await _build_dispatcher(tmp_path)
    response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "thread.recent",
            "params": {"threadId": "thread_self", "limit": 10_000},
        },
        notify=_drop_notify,
    )
    assert response is not None
    assert response["error"]["code"] == INVALID_PARAMS


# ---------------------------------------------------------------------------
# thread.search
# ---------------------------------------------------------------------------


async def test_thread_search_returns_top_match(tmp_path: Path) -> None:
    dispatcher, store = await _build_dispatcher(tmp_path)
    thread = Thread(
        thread_id=new_thread_id(),
        user_scope="self",
        created_at_ms=_now_ms(),
        last_active_at_ms=_now_ms(),
    )
    await store.insert_thread(thread)
    base = _now_ms()
    for i, body in enumerate(
        [
            "discussed q4 metrics with the lead",
            "shipped the auth refactor overnight",
            "drafted a release post for v0.20",
        ]
    ):
        await store.insert_turn(_completed_turn(thread.thread_id, body, at_ms=base + i))

    response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "thread.search",
            "params": {
                "threadId": thread.thread_id,
                "query": "auth refactor",
                "limit": 3,
            },
        },
        notify=_drop_notify,
    )
    assert response is not None
    result = response["result"]
    assert "auth refactor" in result["hits"][0]["body"]
    assert "rank" in result["hits"][0]


async def test_thread_search_rejects_invalid_fts5_query(tmp_path: Path) -> None:
    """Malformed MATCH expressions must surface as INVALID_PARAMS, not
    crash the dispatcher with an OperationalError."""
    dispatcher, store = await _build_dispatcher(tmp_path)
    thread = Thread(
        thread_id=new_thread_id(),
        user_scope="self",
        created_at_ms=_now_ms(),
        last_active_at_ms=_now_ms(),
    )
    await store.insert_thread(thread)
    response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "thread.search",
            "params": {"threadId": thread.thread_id, "query": '"unterminated'},
        },
        notify=_drop_notify,
    )
    assert response is not None
    assert response["error"]["code"] == INVALID_PARAMS


async def test_thread_search_returns_empty_when_no_match(tmp_path: Path) -> None:
    dispatcher, store = await _build_dispatcher(tmp_path)
    thread = Thread(
        thread_id=new_thread_id(),
        user_scope="self",
        created_at_ms=_now_ms(),
        last_active_at_ms=_now_ms(),
    )
    await store.insert_thread(thread)
    await store.insert_turn(_completed_turn(thread.thread_id, "alpha beta", at_ms=_now_ms()))
    response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "thread.search",
            "params": {"threadId": thread.thread_id, "query": "gamma"},
        },
        notify=_drop_notify,
    )
    assert response is not None
    assert response["result"]["hits"] == []


# ---------------------------------------------------------------------------
# digest.latest
# ---------------------------------------------------------------------------


async def test_digest_latest_returns_most_recent(tmp_path: Path) -> None:
    dispatcher, store = await _build_dispatcher(tmp_path)
    thread = Thread(
        thread_id=new_thread_id(),
        user_scope="self",
        created_at_ms=_now_ms(),
        last_active_at_ms=_now_ms(),
    )
    await store.insert_thread(thread)
    await store.insert_digest(
        SessionDigest(
            digest_id=new_digest_id(),
            thread_id=thread.thread_id,
            window_start_ms=1_000,
            window_end_ms=2_000,
            structured_summary={"topics": ["earlier"]},
            second_level_summary_of=None,
        )
    )
    newer = SessionDigest(
        digest_id=new_digest_id(),
        thread_id=thread.thread_id,
        window_start_ms=2_000,
        window_end_ms=3_000,
        structured_summary={"topics": ["fresh"]},
        second_level_summary_of=None,
    )
    await store.insert_digest(newer)

    response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "digest.latest",
            "params": {"threadId": thread.thread_id},
        },
        notify=_drop_notify,
    )
    assert response is not None
    digest = response["result"]["digest"]
    assert digest is not None
    assert digest["digestId"] == newer.digest_id
    assert digest["structuredSummary"]["topics"] == ["fresh"]


async def test_digest_latest_returns_null_for_empty_thread(tmp_path: Path) -> None:
    dispatcher, store = await _build_dispatcher(tmp_path)
    thread = Thread(
        thread_id=new_thread_id(),
        user_scope="self",
        created_at_ms=_now_ms(),
        last_active_at_ms=_now_ms(),
    )
    await store.insert_thread(thread)

    response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "digest.latest",
            "params": {"threadId": thread.thread_id},
        },
        notify=_drop_notify,
    )
    assert response is not None
    assert response["result"]["digest"] is None


# ---------------------------------------------------------------------------
# Stub-replacement check — the four real methods must replace the stubs,
# never sit alongside them. The dispatcher errors on duplicate registration
# so any drift here surfaces as a registration ValueError.
# ---------------------------------------------------------------------------


async def test_real_handlers_compose_with_remaining_stubs(tmp_path: Path) -> None:
    from thalyn_brain.v2_stubs_rpc import register_v2_stubs

    dispatcher, _ = await _build_dispatcher(tmp_path)
    # No exception: the stub registration drops the four methods we now own.
    register_v2_stubs(dispatcher)


@pytest.mark.parametrize("method", ["thread.recent", "thread.search", "digest.latest"])
async def test_no_longer_stubbed_in_v2_stubs(method: str) -> None:
    from thalyn_brain.v2_stubs_rpc import _STUB_METHODS

    method_names = {m for m, _ in _STUB_METHODS}
    assert method not in method_names
