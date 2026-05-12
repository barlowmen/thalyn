"""Structured memory-write surface tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from thalyn_brain.memory import MemoryStore
from thalyn_brain.memory_writes import (
    record_memory_write,
    record_worker_project_memory_write,
)


async def test_write_persists_to_store_and_emits_action_log(tmp_path: Path) -> None:
    store = MemoryStore(data_dir=tmp_path)
    captured: list[tuple[str, Any]] = []

    async def notify(method: str, params: Any) -> None:
        captured.append((method, params))

    entry = await record_memory_write(
        store,
        run_id="r_1",
        body="User prefers tabs over spaces.",
        scope="personal",
        kind="preference",
        author="agent",
        notify=notify,
    )

    # Persisted in the store.
    fetched = await store.get(entry.memory_id)
    assert fetched is not None
    assert fetched.body.startswith("User prefers")
    assert fetched.author == "agent"

    # Emitted as a memory_write action-log entry on the run.
    actions = [params for method, params in captured if method == "run.action_log"]
    assert len(actions) == 1
    payload = actions[0]["entry"]
    assert payload["kind"] == "memory_write"
    assert payload["payload"]["memoryId"] == entry.memory_id
    assert payload["payload"]["author"] == "agent"
    assert payload["payload"]["scope"] == "personal"
    assert payload["payload"]["preview"].startswith("User prefers")


async def test_write_without_notifier_still_persists(tmp_path: Path) -> None:
    store = MemoryStore(data_dir=tmp_path)
    entry = await record_memory_write(
        store,
        run_id="r_x",
        body="A fact.",
        scope="personal",
        kind="fact",
        author="user",
    )
    fetched = await store.get(entry.memory_id)
    assert fetched is not None


async def test_write_truncates_preview_for_long_bodies(tmp_path: Path) -> None:
    store = MemoryStore(data_dir=tmp_path)
    captured: list[tuple[str, Any]] = []

    async def notify(method: str, params: Any) -> None:
        captured.append((method, params))

    long_body = "x" * 500
    await record_memory_write(
        store,
        run_id="r_2",
        body=long_body,
        scope="agent",
        kind="reference",
        author="agent",
        agent_id="agent_brain",
        notify=notify,
    )

    actions = [params for method, params in captured if method == "run.action_log"]
    preview = actions[0]["entry"]["payload"]["preview"]
    assert len(preview) <= 240
    assert preview.endswith("…")


async def test_empty_body_is_rejected(tmp_path: Path) -> None:
    store = MemoryStore(data_dir=tmp_path)
    with pytest.raises(ValueError):
        await record_memory_write(
            store,
            run_id="r_x",
            body="   ",
            scope="personal",
            kind="fact",
            author="user",
        )


# ---------------------------------------------------------------------------
# Worker-through-lead project memory
# ---------------------------------------------------------------------------


async def test_worker_project_write_persists_and_records_lead_provenance(
    tmp_path: Path,
) -> None:
    store = MemoryStore(data_dir=tmp_path)
    captured: list[tuple[str, Any]] = []

    async def notify(method: str, params: Any) -> None:
        captured.append((method, params))

    entry = await record_worker_project_memory_write(
        store,
        run_id="r_42",
        project_id="proj_alpha",
        body="rate-limit redesign decision: token bucket per user.",
        kind="reference",
        worker_author="worker_codegen",
        via_lead_id="lead_alpha",
        notify=notify,
    )

    fetched = await store.get(entry.memory_id)
    assert fetched is not None
    assert fetched.scope == "project"
    assert fetched.project_id == "proj_alpha"
    assert fetched.author == "worker_codegen"

    actions = [params for method, params in captured if method == "run.action_log"]
    assert len(actions) == 1
    payload = actions[0]["entry"]["payload"]
    assert payload["scope"] == "project"
    assert payload["author"] == "worker_codegen"
    assert payload["viaLeadId"] == "lead_alpha"
    assert payload["writerRole"] == "worker"


async def test_worker_project_write_requires_project_id(tmp_path: Path) -> None:
    store = MemoryStore(data_dir=tmp_path)
    with pytest.raises(ValueError):
        await record_worker_project_memory_write(
            store,
            run_id="r_x",
            project_id="",
            body="some note",
            kind="reference",
            worker_author="worker",
            via_lead_id="lead_x",
        )


async def test_worker_project_write_requires_lead_id(tmp_path: Path) -> None:
    store = MemoryStore(data_dir=tmp_path)
    with pytest.raises(ValueError):
        await record_worker_project_memory_write(
            store,
            run_id="r_x",
            project_id="proj_x",
            body="note",
            kind="reference",
            worker_author="worker",
            via_lead_id="",
        )


async def test_brain_write_omits_lead_and_role_in_payload(tmp_path: Path) -> None:
    """Brain-direct writes (no via_lead_id) should not surface
    ``viaLeadId`` or ``writerRole`` so the renderer can distinguish
    them from worker-through-lead writes."""
    store = MemoryStore(data_dir=tmp_path)
    captured: list[tuple[str, Any]] = []

    async def notify(method: str, params: Any) -> None:
        captured.append((method, params))

    await record_memory_write(
        store,
        run_id="r_b",
        body="User prefers tabs.",
        scope="personal",
        kind="preference",
        author="agent_brain",
        notify=notify,
    )

    payload = captured[0][1]["entry"]["payload"]
    assert "viaLeadId" not in payload
    assert "writerRole" not in payload
