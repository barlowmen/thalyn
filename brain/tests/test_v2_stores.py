"""CRUD tests for the v2 brain-side stores.

Each new table introduced by migration 003 has a Python store class
under ``thalyn_brain/`` and exercises insert / get / list / delete /
domain-specific update via the methods below. Tests share a single
``tmp_path`` because the migration runner is process-local; each test
opens a clean directory.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
from thalyn_brain.action_log import (
    ActionLogEntry,
    ActionLogStore,
    new_action_id,
)
from thalyn_brain.agents import (
    AgentRecord,
    AgentRecordsStore,
    new_agent_id,
)
from thalyn_brain.approvals import (
    Approval,
    ApprovalsStore,
    new_approval_id,
)
from thalyn_brain.auth_backends import (
    AuthBackend,
    AuthBackendsStore,
    new_auth_backend_id,
)
from thalyn_brain.projects import Project, ProjectsStore, new_project_id
from thalyn_brain.routing import (
    RoutingOverride,
    RoutingOverridesStore,
    new_routing_override_id,
)
from thalyn_brain.runs import RunHeader, RunsStore
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


def _agent(**overrides: Any) -> AgentRecord:
    base: dict[str, Any] = {
        "agent_id": new_agent_id(),
        "kind": "lead",
        "display_name": "Lead-Test",
        "parent_agent_id": None,
        "project_id": None,
        "scope_facet": None,
        "memory_namespace": "test",
        "default_provider_id": "anthropic",
        "system_prompt": "",
        "status": "active",
        "created_at_ms": _now_ms(),
        "last_active_at_ms": _now_ms(),
    }
    base.update(overrides)
    return AgentRecord(**base)


def _project(**overrides: Any) -> Project:
    base: dict[str, Any] = {
        "project_id": new_project_id(),
        "name": "Test Project",
        "slug": "test-project",
        "workspace_path": None,
        "repo_remote": None,
        "lead_agent_id": None,
        "memory_namespace": "test",
        "conversation_tag": "Test",
        "roadmap": "",
        "provider_config": None,
        "connector_grants": None,
        "local_only": False,
        "status": "active",
        "created_at_ms": _now_ms(),
        "last_active_at_ms": _now_ms(),
    }
    base.update(overrides)
    return Project(**base)


# ---------------------------------------------------------------------------
# AgentRecord
# ---------------------------------------------------------------------------


async def test_agent_records_crud_round_trip(tmp_path: Path) -> None:
    store = AgentRecordsStore(data_dir=tmp_path)
    record = _agent(kind="brain", display_name="Thalyn")
    await store.insert(record)
    fetched = await store.get(record.agent_id)
    assert fetched is not None
    assert fetched.display_name == "Thalyn"
    assert (await store.delete(record.agent_id)) is True
    assert (await store.get(record.agent_id)) is None


async def test_agent_records_filter_by_kind_and_project(tmp_path: Path) -> None:
    store = AgentRecordsStore(data_dir=tmp_path)
    await store.insert(_agent(kind="brain", display_name="Thalyn-Test"))
    await store.insert(_agent(kind="lead", display_name="Lead-A"))
    await store.insert(_agent(kind="lead", display_name="Lead-B"))
    # Migration 004 seeds a default brain ("Thalyn") and a default
    # lead ("Lead-Default"); the filter must return them alongside the
    # test's inserts.
    leads = await store.list_all(kind="lead")
    lead_names = {r.display_name for r in leads}
    assert {"Lead-A", "Lead-B"} <= lead_names
    brains = await store.list_all(kind="brain")
    brain_names = {r.display_name for r in brains}
    assert {"Thalyn-Test"} <= brain_names


async def test_agent_records_update_status(tmp_path: Path) -> None:
    store = AgentRecordsStore(data_dir=tmp_path)
    record = _agent()
    await store.insert(record)
    later = _now_ms() + 1000
    assert (await store.update_status(record.agent_id, "paused", last_active_at_ms=later)) is True
    fetched = await store.get(record.agent_id)
    assert fetched is not None
    assert fetched.status == "paused"
    assert fetched.last_active_at_ms == later


async def test_agent_records_rejects_invalid_kind(tmp_path: Path) -> None:
    store = AgentRecordsStore(data_dir=tmp_path)
    with pytest.raises(ValueError):
        await store.insert(_agent(kind="not-a-real-kind"))


# ---------------------------------------------------------------------------
# Project
# ---------------------------------------------------------------------------


async def test_projects_crud_round_trip(tmp_path: Path) -> None:
    store = ProjectsStore(data_dir=tmp_path)
    project = _project(slug="alpha")
    await store.insert(project)
    fetched = await store.get(project.project_id)
    assert fetched is not None
    assert fetched.slug == "alpha"
    by_slug = await store.get_by_slug("alpha")
    assert by_slug is not None
    assert by_slug.project_id == project.project_id
    assert (await store.delete(project.project_id)) is True


async def test_projects_set_lead_links_to_agent(tmp_path: Path) -> None:
    agents = AgentRecordsStore(data_dir=tmp_path)
    projects = ProjectsStore(data_dir=tmp_path)
    project = _project(slug="beta")
    await projects.insert(project)
    lead = _agent(kind="lead", display_name="Lead-Beta", project_id=project.project_id)
    await agents.insert(lead)
    assert (await projects.set_lead(project.project_id, lead.agent_id)) is True
    fetched = await projects.get(project.project_id)
    assert fetched is not None
    assert fetched.lead_agent_id == lead.agent_id


async def test_projects_slug_must_be_unique(tmp_path: Path) -> None:
    store = ProjectsStore(data_dir=tmp_path)
    await store.insert(_project(slug="gamma"))
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        await store.insert(_project(slug="gamma"))


# ---------------------------------------------------------------------------
# Thread / ThreadTurn / SessionDigest
# ---------------------------------------------------------------------------


async def test_threads_crud_round_trip(tmp_path: Path) -> None:
    store = ThreadsStore(data_dir=tmp_path)
    thread = Thread(
        thread_id=new_thread_id(),
        user_scope="self",
        created_at_ms=_now_ms(),
        last_active_at_ms=_now_ms(),
    )
    await store.insert_thread(thread)
    fetched = await store.get_thread(thread.thread_id)
    assert fetched is not None
    later = _now_ms() + 5
    assert (await store.touch_thread(thread.thread_id, later)) is True
    fetched2 = await store.get_thread(thread.thread_id)
    assert fetched2 is not None
    assert fetched2.last_active_at_ms == later


async def test_thread_turns_round_trip_and_ordering(tmp_path: Path) -> None:
    store = ThreadsStore(data_dir=tmp_path)
    thread = Thread(
        thread_id=new_thread_id(),
        user_scope="self",
        created_at_ms=_now_ms(),
        last_active_at_ms=_now_ms(),
    )
    await store.insert_thread(thread)
    base_ms = _now_ms()
    await store.insert_turn(
        ThreadTurn(
            turn_id=new_turn_id(),
            thread_id=thread.thread_id,
            project_id=None,
            agent_id=None,
            role="user",
            body="hi",
            provenance=None,
            confidence=None,
            episodic_index_ptr=None,
            at_ms=base_ms,
        )
    )
    await store.insert_turn(
        ThreadTurn(
            turn_id=new_turn_id(),
            thread_id=thread.thread_id,
            project_id=None,
            agent_id=None,
            role="brain",
            body="hello",
            provenance={"source": "thalyn"},
            confidence=None,
            episodic_index_ptr=None,
            at_ms=base_ms + 10,
        )
    )
    turns = await store.list_turns(thread.thread_id)
    assert [t.body for t in turns] == ["hi", "hello"]
    assert turns[1].provenance == {"source": "thalyn"}


async def test_thread_turn_rejects_invalid_role(tmp_path: Path) -> None:
    store = ThreadsStore(data_dir=tmp_path)
    thread = Thread(
        thread_id=new_thread_id(),
        user_scope="self",
        created_at_ms=_now_ms(),
        last_active_at_ms=_now_ms(),
    )
    await store.insert_thread(thread)
    with pytest.raises(ValueError):
        await store.insert_turn(
            ThreadTurn(
                turn_id=new_turn_id(),
                thread_id=thread.thread_id,
                project_id=None,
                agent_id=None,
                role="impostor",
                body="x",
                provenance=None,
                confidence=None,
                episodic_index_ptr=None,
                at_ms=_now_ms(),
            )
        )


async def test_session_digest_round_trip(tmp_path: Path) -> None:
    store = ThreadsStore(data_dir=tmp_path)
    thread = Thread(
        thread_id=new_thread_id(),
        user_scope="self",
        created_at_ms=_now_ms(),
        last_active_at_ms=_now_ms(),
    )
    await store.insert_thread(thread)
    digest = SessionDigest(
        digest_id=new_digest_id(),
        thread_id=thread.thread_id,
        window_start_ms=_now_ms(),
        window_end_ms=_now_ms() + 1000,
        structured_summary={"topics": ["auth"], "decisions": [], "open_threads": []},
        second_level_summary_of=None,
    )
    await store.insert_digest(digest)
    digests = await store.list_digests(thread.thread_id)
    assert len(digests) == 1
    assert digests[0].structured_summary["topics"] == ["auth"]


# ---------------------------------------------------------------------------
# AuthBackend
# ---------------------------------------------------------------------------


async def test_auth_backends_crud_round_trip(tmp_path: Path) -> None:
    store = AuthBackendsStore(data_dir=tmp_path)
    backend = AuthBackend(
        auth_backend_id=new_auth_backend_id(),
        kind="claude_subscription",
        config={"keychain_entry": "claude_cli_token"},
    )
    await store.insert(backend)
    fetched = await store.get(backend.auth_backend_id)
    assert fetched is not None
    assert fetched.kind == "claude_subscription"
    assert fetched.config["keychain_entry"] == "claude_cli_token"


async def test_auth_backends_rejects_invalid_kind(tmp_path: Path) -> None:
    store = AuthBackendsStore(data_dir=tmp_path)
    with pytest.raises(ValueError):
        await store.insert(
            AuthBackend(
                auth_backend_id=new_auth_backend_id(),
                kind="not-a-real-kind",
                config={},
            )
        )


# ---------------------------------------------------------------------------
# RoutingOverride
# ---------------------------------------------------------------------------


async def test_routing_override_upsert_replaces(tmp_path: Path) -> None:
    projects = ProjectsStore(data_dir=tmp_path)
    routing = RoutingOverridesStore(data_dir=tmp_path)
    project = _project(slug="delta")
    await projects.insert(project)
    await routing.upsert(
        RoutingOverride(
            routing_override_id=new_routing_override_id(),
            project_id=project.project_id,
            task_tag="coding",
            provider_id="anthropic",
            updated_at_ms=_now_ms(),
        )
    )
    # Upsert with the same (project, tag) should replace, not duplicate.
    await routing.upsert(
        RoutingOverride(
            routing_override_id=new_routing_override_id(),
            project_id=project.project_id,
            task_tag="coding",
            provider_id="ollama",
            updated_at_ms=_now_ms() + 1,
        )
    )
    fetched = await routing.get(project.project_id, "coding")
    assert fetched is not None
    assert fetched.provider_id == "ollama"
    overrides = await routing.list_for_project(project.project_id)
    assert len(overrides) == 1


# ---------------------------------------------------------------------------
# Approval
# ---------------------------------------------------------------------------


async def test_approvals_crud_and_resolve(tmp_path: Path) -> None:
    runs = RunsStore(data_dir=tmp_path)
    approvals = ApprovalsStore(data_dir=tmp_path)
    run = RunHeader(
        run_id="run_1",
        project_id=None,
        parent_run_id=None,
        status="planning",
        title="t",
        provider_id="anthropic",
        started_at_ms=_now_ms(),
        completed_at_ms=None,
        drift_score=0.0,
        final_response="",
    )
    await runs.insert(run)
    approval = Approval(
        approval_id=new_approval_id(),
        run_id=run.run_id,
        gate_kind="plan",
        status="pending",
        context={"reason": "plan-needs-review"},
        requested_at_ms=_now_ms(),
        resolved_at_ms=None,
    )
    await approvals.insert(approval)
    pending = await approvals.list_pending()
    assert any(a.approval_id == approval.approval_id for a in pending)
    later = _now_ms() + 5
    assert (await approvals.resolve(approval.approval_id, "approved", later)) is True
    fetched = await approvals.get(approval.approval_id)
    assert fetched is not None
    assert fetched.status == "approved"
    assert fetched.resolved_at_ms == later
    # Resolving again should be a no-op (status already non-pending).
    assert (await approvals.resolve(approval.approval_id, "rejected", later + 1)) is False


async def test_approvals_rejects_invalid_gate(tmp_path: Path) -> None:
    store = ApprovalsStore(data_dir=tmp_path)
    with pytest.raises(ValueError):
        await store.insert(
            Approval(
                approval_id=new_approval_id(),
                run_id="run_x",
                gate_kind="not-a-real-gate",
                status="pending",
                context=None,
                requested_at_ms=_now_ms(),
                resolved_at_ms=None,
            )
        )


# ---------------------------------------------------------------------------
# ActionLog
# ---------------------------------------------------------------------------


async def test_action_log_append_and_list_for_run(tmp_path: Path) -> None:
    runs = RunsStore(data_dir=tmp_path)
    log = ActionLogStore(data_dir=tmp_path)
    run = RunHeader(
        run_id="run_log",
        project_id=None,
        parent_run_id=None,
        status="running",
        title="t",
        provider_id="anthropic",
        started_at_ms=_now_ms(),
        completed_at_ms=None,
        drift_score=0.0,
        final_response="",
    )
    await runs.insert(run)
    await log.append(
        ActionLogEntry(
            action_id=new_action_id(),
            run_id=run.run_id,
            at_ms=_now_ms(),
            kind="tool_call",
            payload={"tool": "browser_navigate"},
        )
    )
    await log.append(
        ActionLogEntry(
            action_id=new_action_id(),
            run_id=run.run_id,
            at_ms=_now_ms() + 1,
            kind="llm_call",
            payload=None,
        )
    )
    entries = await log.list_for_run(run.run_id)
    assert [e.kind for e in entries] == ["tool_call", "llm_call"]
    only_tools = await log.list_for_run(run.run_id, kind="tool_call")
    assert len(only_tools) == 1


async def test_action_log_rejects_invalid_kind(tmp_path: Path) -> None:
    store = ActionLogStore(data_dir=tmp_path)
    with pytest.raises(ValueError):
        await store.append(
            ActionLogEntry(
                action_id=new_action_id(),
                run_id="run_z",
                at_ms=_now_ms(),
                kind="not-a-real-kind",
                payload=None,
            )
        )
