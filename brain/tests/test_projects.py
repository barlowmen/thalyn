"""Tests for ``ProjectsStore`` factory + lifecycle helpers (v0.31).

The store's CRUD-on-a-pre-built-row shape is exercised indirectly by
``test_lead_lifecycle`` and the seed migration. This module covers
the v0.31 additions: ``create`` (slug derivation + collision
suffixing + memory-namespace mirroring), ``update_name``,
``set_status``, ``touch_active_at``, and the ``slugify`` helper.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from thalyn_brain.projects import (
    PROJECT_STATUSES,
    ProjectsStore,
    slugify,
)


def test_slugify_kebabs_arbitrary_input() -> None:
    assert slugify("Tax Prep 2026") == "tax-prep-2026"
    assert slugify("  Plan the Q3 Offsite  ") == "plan-the-q3-offsite"
    assert slugify("Learn Rust!") == "learn-rust"


def test_slugify_falls_back_when_empty() -> None:
    fallback = slugify("✨")
    assert fallback.startswith("project-")
    assert len(fallback) > len("project-")


def test_slugify_caps_long_input() -> None:
    long_name = "x" * 200
    out = slugify(long_name)
    assert len(out) <= 48


async def test_create_derives_slug_and_namespace(tmp_path: Path) -> None:
    store = ProjectsStore(data_dir=tmp_path)
    project = await store.create(name="Tax Prep 2026")
    assert project.name == "Tax Prep 2026"
    assert project.slug == "tax-prep-2026"
    assert project.memory_namespace == "tax-prep-2026"
    assert project.conversation_tag == "Tax Prep 2026"
    assert project.status == "active"
    assert project.lead_agent_id is None
    assert project.created_at_ms == project.last_active_at_ms


async def test_create_appends_suffix_on_slug_collision(tmp_path: Path) -> None:
    store = ProjectsStore(data_dir=tmp_path)
    first = await store.create(name="Alpha")
    second = await store.create(name="Alpha")
    third = await store.create(name="Alpha")
    assert first.slug == "alpha"
    assert second.slug == "alpha-2"
    assert third.slug == "alpha-3"
    # Collision suffixing doesn't touch the user-facing display name.
    assert first.name == second.name == third.name == "Alpha"


async def test_create_rejects_empty_name(tmp_path: Path) -> None:
    store = ProjectsStore(data_dir=tmp_path)
    with pytest.raises(ValueError):
        await store.create(name="   ")


async def test_update_name_persists(tmp_path: Path) -> None:
    store = ProjectsStore(data_dir=tmp_path)
    project = await store.create(name="Original")
    updated = await store.update_name(project.project_id, "Renamed")
    assert updated is True
    refreshed = await store.get(project.project_id)
    assert refreshed is not None
    assert refreshed.name == "Renamed"
    # Slug stays — a rename through this surface only flips the
    # human-facing label.
    assert refreshed.slug == project.slug


async def test_update_name_returns_false_for_unknown_project(tmp_path: Path) -> None:
    store = ProjectsStore(data_dir=tmp_path)
    updated = await store.update_name("proj_missing", "anything")
    assert updated is False


async def test_update_name_rejects_empty(tmp_path: Path) -> None:
    store = ProjectsStore(data_dir=tmp_path)
    project = await store.create(name="Original")
    with pytest.raises(ValueError):
        await store.update_name(project.project_id, "")


async def test_set_status_transitions(tmp_path: Path) -> None:
    store = ProjectsStore(data_dir=tmp_path)
    project = await store.create(name="Alpha")
    for status in ("paused", "active", "archived"):
        assert status in PROJECT_STATUSES
        flipped = await store.set_status(project.project_id, status)
        assert flipped is True
        refreshed = await store.get(project.project_id)
        assert refreshed is not None
        assert refreshed.status == status


async def test_set_status_rejects_invalid_value(tmp_path: Path) -> None:
    store = ProjectsStore(data_dir=tmp_path)
    project = await store.create(name="Alpha")
    with pytest.raises(ValueError):
        await store.set_status(project.project_id, "rotting")


async def test_touch_active_at_advances_recency(tmp_path: Path) -> None:
    store = ProjectsStore(data_dir=tmp_path)
    project = await store.create(name="Alpha")
    later = project.last_active_at_ms + 60_000
    flipped = await store.touch_active_at(project.project_id, later)
    assert flipped is True
    refreshed = await store.get(project.project_id)
    assert refreshed is not None
    assert refreshed.last_active_at_ms == later


async def test_touch_active_at_defaults_to_now(tmp_path: Path) -> None:
    store = ProjectsStore(data_dir=tmp_path)
    project = await store.create(name="Alpha")
    before = int(time.time() * 1000)
    flipped = await store.touch_active_at(project.project_id)
    after = int(time.time() * 1000) + 1
    assert flipped is True
    refreshed = await store.get(project.project_id)
    assert refreshed is not None
    assert before <= refreshed.last_active_at_ms <= after


async def test_list_filters_by_status(tmp_path: Path) -> None:
    store = ProjectsStore(data_dir=tmp_path)
    alpha = await store.create(name="Alpha")
    beta = await store.create(name="Beta")
    gamma = await store.create(name="Gamma")
    await store.set_status(beta.project_id, "paused")
    await store.set_status(gamma.project_id, "archived")

    active_ids = {p.project_id for p in await store.list_all(status="active")}
    paused_ids = {p.project_id for p in await store.list_all(status="paused")}
    archived_ids = {p.project_id for p in await store.list_all(status="archived")}

    assert alpha.project_id in active_ids
    assert beta.project_id in paused_ids
    assert gamma.project_id in archived_ids
    assert beta.project_id not in active_ids
