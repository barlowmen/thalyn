"""Memory store + JSON-RPC bindings tests."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
from thalyn_brain.memory import MemoryEntry, MemoryStore, MemoryUpdate, new_memory_id
from thalyn_brain.memory_rpc import register_memory_methods
from thalyn_brain.rpc import Dispatcher


def _entry(**overrides: Any) -> MemoryEntry:
    base: dict[str, Any] = {
        "memory_id": new_memory_id(),
        "project_id": None,
        "scope": "personal",
        "kind": "preference",
        "body": "Tabs over spaces.",
        "author": "user",
        "created_at_ms": int(time.time() * 1000),
        "updated_at_ms": int(time.time() * 1000),
    }
    base.update(overrides)
    return MemoryEntry(**base)


# ---------------------------------------------------------------------------
# Store round-trip
# ---------------------------------------------------------------------------


async def test_insert_and_get_round_trip(tmp_path: Path) -> None:
    store = MemoryStore(data_dir=tmp_path)
    entry = _entry()
    await store.insert(entry)
    fetched = await store.get(entry.memory_id)
    assert fetched is not None
    assert fetched.body == "Tabs over spaces."


async def test_list_filters_by_scope_and_orders_by_created_desc(tmp_path: Path) -> None:
    store = MemoryStore(data_dir=tmp_path)
    base = int(time.time() * 1000)
    await store.insert(_entry(scope="personal", created_at_ms=base, updated_at_ms=base))
    await store.insert(
        _entry(
            scope="project",
            created_at_ms=base + 100,
            updated_at_ms=base + 100,
        )
    )
    await store.insert(
        _entry(
            scope="agent",
            created_at_ms=base + 200,
            updated_at_ms=base + 200,
        )
    )
    await store.insert(
        _entry(
            scope="episodic",
            created_at_ms=base + 300,
            updated_at_ms=base + 300,
        )
    )

    personal = await store.list_entries(scopes=["personal"])
    assert [e.scope for e in personal] == ["personal"]
    all_entries = await store.list_entries()
    assert [e.scope for e in all_entries] == [
        "episodic",
        "agent",
        "project",
        "personal",
    ]


async def test_search_finds_substring_matches(tmp_path: Path) -> None:
    store = MemoryStore(data_dir=tmp_path)
    await store.insert(_entry(body="prefers tabs over spaces"))
    await store.insert(_entry(body="reviewer wants double-quotes"))
    hits = await store.search("tabs")
    assert len(hits) == 1
    assert "tabs" in hits[0].body


async def test_update_changes_body_and_kind(tmp_path: Path) -> None:
    store = MemoryStore(data_dir=tmp_path)
    entry = _entry()
    await store.insert(entry)
    updated = await store.update(
        entry.memory_id,
        MemoryUpdate().with_body("Spaces over tabs (changed mind).").with_kind("fact"),
    )
    assert updated
    fetched = await store.get(entry.memory_id)
    assert fetched is not None
    assert fetched.body.startswith("Spaces")
    assert fetched.kind == "fact"
    assert fetched.updated_at_ms >= entry.updated_at_ms


async def test_delete_returns_true_only_when_row_existed(tmp_path: Path) -> None:
    store = MemoryStore(data_dir=tmp_path)
    entry = _entry()
    await store.insert(entry)
    assert await store.delete(entry.memory_id) is True
    assert await store.delete(entry.memory_id) is False


async def test_invalid_scope_or_kind_rejected_at_insert(tmp_path: Path) -> None:
    store = MemoryStore(data_dir=tmp_path)
    with pytest.raises(ValueError):
        await store.insert(_entry(scope="bogus"))
    with pytest.raises(ValueError):
        await store.insert(_entry(kind="bogus"))


async def test_ephemeral_tiers_are_rejected_at_insert(tmp_path: Path) -> None:
    """``working`` and ``session`` are recognized in the five-tier
    vocabulary but never persist as ``MEMORY_ENTRY`` rows — the store
    rejects them so a bug elsewhere can't quietly land an in-memory
    tier in ``app.db``."""
    store = MemoryStore(data_dir=tmp_path)
    with pytest.raises(ValueError):
        await store.insert(_entry(scope="working"))
    with pytest.raises(ValueError):
        await store.insert(_entry(scope="session"))


async def test_legacy_user_scope_rejected_after_rename(tmp_path: Path) -> None:
    """The migration renames v1 ``user`` rows to ``personal`` once;
    fresh writes carrying the legacy scope are rejected so callers
    that still send ``user`` are flagged loudly."""
    store = MemoryStore(data_dir=tmp_path)
    with pytest.raises(ValueError):
        await store.insert(_entry(scope="user"))


# ---------------------------------------------------------------------------
# JSON-RPC surface
# ---------------------------------------------------------------------------


async def test_rpc_add_and_list_round_trip(tmp_path: Path) -> None:
    store = MemoryStore(data_dir=tmp_path)
    dispatcher = Dispatcher()
    register_memory_methods(dispatcher, store)

    async def notify(method: str, params: Any) -> None:
        del method, params

    add_response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "memory.add",
            "params": {
                "body": "User prefers tabs over spaces.",
                "scope": "personal",
                "kind": "preference",
                "author": "agent",
            },
        },
        notify,
    )
    assert add_response is not None
    entry = add_response["result"]["entry"]
    assert entry["body"].startswith("User prefers")
    assert entry["author"] == "agent"

    list_response = await dispatcher.handle(
        {"jsonrpc": "2.0", "id": 2, "method": "memory.list"},
        notify,
    )
    assert list_response is not None
    entries = list_response["result"]["entries"]
    assert len(entries) == 1
    assert entries[0]["memoryId"] == entry["memoryId"]


async def test_rpc_add_rejects_invalid_inputs(tmp_path: Path) -> None:
    store = MemoryStore(data_dir=tmp_path)
    dispatcher = Dispatcher()
    register_memory_methods(dispatcher, store)

    async def notify(method: str, params: Any) -> None:
        del method, params

    response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "memory.add",
            "params": {"body": "hi", "scope": "bogus", "kind": "fact", "author": "a"},
        },
        notify,
    )
    assert response is not None
    assert response["error"]["code"] == -32602


async def test_rpc_update_and_delete(tmp_path: Path) -> None:
    store = MemoryStore(data_dir=tmp_path)
    dispatcher = Dispatcher()
    register_memory_methods(dispatcher, store)

    async def notify(method: str, params: Any) -> None:
        del method, params

    add = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "memory.add",
            "params": {
                "body": "v1",
                "scope": "personal",
                "kind": "fact",
                "author": "user",
            },
        },
        notify,
    )
    assert add is not None
    memory_id = add["result"]["entry"]["memoryId"]

    update = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "memory.update",
            "params": {"memoryId": memory_id, "body": "v2"},
        },
        notify,
    )
    assert update is not None
    assert update["result"]["entry"]["body"] == "v2"

    delete = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "memory.delete",
            "params": {"memoryId": memory_id},
        },
        notify,
    )
    assert delete is not None
    assert delete["result"]["deleted"] is True


async def test_rpc_search_via_dispatcher(tmp_path: Path) -> None:
    store = MemoryStore(data_dir=tmp_path)
    dispatcher = Dispatcher()
    register_memory_methods(dispatcher, store)
    await store.insert(_entry(body="prefers tabs over spaces"))

    async def notify(method: str, params: Any) -> None:
        del method, params

    response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "memory.search",
            "params": {"query": "tabs"},
        },
        notify,
    )
    assert response is not None
    entries = response["result"]["entries"]
    assert len(entries) == 1
    assert "tabs" in entries[0]["body"]


async def test_search_scope_filter_narrows_results(tmp_path: Path) -> None:
    """Explicit recall against ``personal`` memory should not pull
    project-scoped rows even when they match the query."""
    store = MemoryStore(data_dir=tmp_path)
    await store.insert(_entry(scope="personal", body="auto-merge: never"))
    await store.insert(
        _entry(
            scope="project",
            body="auto-merge: enabled for docs repo",
            project_id="proj_default",
        )
    )

    personal = await store.search("auto-merge", scopes=["personal"])
    assert [e.scope for e in personal] == ["personal"]

    project = await store.search("auto-merge", scopes=["project"])
    assert [e.scope for e in project] == ["project"]


async def test_rpc_search_accepts_scopes_filter(tmp_path: Path) -> None:
    store = MemoryStore(data_dir=tmp_path)
    dispatcher = Dispatcher()
    register_memory_methods(dispatcher, store)
    await store.insert(_entry(scope="personal", body="prefers tabs"))
    await store.insert(_entry(scope="project", body="repo prefers tabs"))

    async def notify(method: str, params: Any) -> None:
        del method, params

    response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "memory.search",
            "params": {"query": "tabs", "scopes": ["personal"]},
        },
        notify,
    )
    assert response is not None
    entries = response["result"]["entries"]
    assert {e["scope"] for e in entries} == {"personal"}
