"""Tests for the ``project.merge`` JSON-RPC method.

Covers the two-phase shape — dry-run returns the plan without mutating
state, apply runs the transaction and reports the outcome. Error paths
(missing stores, same-project merge, archived target) surface as
``INVALID_PARAMS`` so the renderer can show a useful message.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from thalyn_brain.agents import AgentRecordsStore
from thalyn_brain.lead_lifecycle import LeadLifecycle, SpawnRequest
from thalyn_brain.memory import MemoryStore
from thalyn_brain.project_rpc import register_project_methods
from thalyn_brain.projects import ProjectsStore
from thalyn_brain.routing import RoutingOverridesStore
from thalyn_brain.rpc import INVALID_PARAMS, Dispatcher
from thalyn_brain.threads import (
    Thread,
    ThreadsStore,
    ThreadTurn,
    new_thread_id,
    new_turn_id,
)


class _DropNotify:
    async def __call__(self, method: str, params: Any) -> None:
        return None


_drop_notify = _DropNotify()


def _now_ms() -> int:
    return int(time.time() * 1000)


async def _setup(
    tmp_path: Path,
    *,
    full: bool = True,
) -> tuple[Dispatcher, ProjectsStore, ThreadsStore, LeadLifecycle, AgentRecordsStore]:
    projects = ProjectsStore(data_dir=tmp_path)
    agents = AgentRecordsStore(data_dir=tmp_path)
    threads = ThreadsStore(data_dir=tmp_path)
    memory = MemoryStore(data_dir=tmp_path)
    routing_overrides = RoutingOverridesStore(data_dir=tmp_path)
    lifecycle = LeadLifecycle(agents=agents, projects=projects)
    dispatcher = Dispatcher()
    if full:
        register_project_methods(
            dispatcher,
            projects=projects,
            lead_lifecycle=lifecycle,
            threads=threads,
            memory=memory,
            agents=agents,
            routing_overrides=routing_overrides,
            data_dir=tmp_path,
        )
    else:
        register_project_methods(
            dispatcher,
            projects=projects,
            lead_lifecycle=lifecycle,
        )
    return dispatcher, projects, threads, lifecycle, agents


async def _call(
    dispatcher: Dispatcher,
    method: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    response = await dispatcher.handle(
        {"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        notify=_drop_notify,
    )
    assert response is not None
    return response


async def _seed_thread(threads: ThreadsStore) -> Thread:
    thread = Thread(
        thread_id=new_thread_id(),
        user_scope="self",
        created_at_ms=_now_ms(),
        last_active_at_ms=_now_ms(),
    )
    await threads.insert_thread(thread)
    return thread


async def _seed_turn(
    threads: ThreadsStore,
    thread: Thread,
    project_id: str,
    body: str,
) -> ThreadTurn:
    turn = ThreadTurn(
        turn_id=new_turn_id(),
        thread_id=thread.thread_id,
        project_id=project_id,
        agent_id=None,
        role="user",
        body=body,
        provenance=None,
        confidence=None,
        episodic_index_ptr=None,
        at_ms=_now_ms(),
    )
    await threads.insert_turn(turn)
    return turn


async def test_merge_dry_run_returns_plan_without_mutating(tmp_path: Path) -> None:
    dispatcher, projects, threads, lifecycle, _ = await _setup(tmp_path)
    absorbed = await projects.create(name="UI")
    surviving = await projects.create(name="Thalyn")
    await lifecycle.spawn(SpawnRequest(project_id=absorbed.project_id))
    await lifecycle.spawn(SpawnRequest(project_id=surviving.project_id))
    thread = await _seed_thread(threads)
    await _seed_turn(threads, thread, absorbed.project_id, "ui work")
    await _seed_turn(threads, thread, surviving.project_id, "thalyn work")

    response = await _call(
        dispatcher,
        "project.merge",
        {
            "fromProjectId": absorbed.project_id,
            "intoProjectId": surviving.project_id,
        },
    )
    assert "result" in response
    result = response["result"]
    assert result["plan"]["counts"]["threadTurns"] == 1
    assert result["outcome"] is None

    # Nothing actually moved.
    absorbed_after = await projects.get(absorbed.project_id)
    assert absorbed_after is not None
    assert absorbed_after.status == "active"


async def test_merge_apply_runs_the_plan(tmp_path: Path) -> None:
    dispatcher, projects, threads, lifecycle, _ = await _setup(tmp_path)
    absorbed = await projects.create(name="UI")
    surviving = await projects.create(name="Thalyn")
    await lifecycle.spawn(SpawnRequest(project_id=absorbed.project_id))
    await lifecycle.spawn(SpawnRequest(project_id=surviving.project_id))
    thread = await _seed_thread(threads)
    await _seed_turn(threads, thread, absorbed.project_id, "ui work")

    response = await _call(
        dispatcher,
        "project.merge",
        {
            "fromProjectId": absorbed.project_id,
            "intoProjectId": surviving.project_id,
            "apply": True,
        },
    )
    assert "result" in response
    result = response["result"]
    assert result["outcome"] is not None
    assert result["outcome"]["threadTurnsRewritten"] == 1
    assert result["outcome"]["absorbedLeadArchived"] is True

    absorbed_after = await projects.get(absorbed.project_id)
    assert absorbed_after is not None
    assert absorbed_after.status == "archived"


async def test_merge_rejects_same_project(tmp_path: Path) -> None:
    dispatcher, projects, _threads, _lifecycle, _ = await _setup(tmp_path)
    project = await projects.create(name="Solo")
    response = await _call(
        dispatcher,
        "project.merge",
        {
            "fromProjectId": project.project_id,
            "intoProjectId": project.project_id,
        },
    )
    assert "error" in response
    assert response["error"]["code"] == INVALID_PARAMS
    assert "itself" in response["error"]["message"]


async def test_merge_rejects_archived_target(tmp_path: Path) -> None:
    dispatcher, projects, _threads, _lifecycle, _ = await _setup(tmp_path)
    absorbed = await projects.create(name="UI")
    surviving = await projects.create(name="Thalyn")
    await projects.set_status(surviving.project_id, "archived")
    response = await _call(
        dispatcher,
        "project.merge",
        {
            "fromProjectId": absorbed.project_id,
            "intoProjectId": surviving.project_id,
        },
    )
    assert "error" in response
    assert response["error"]["code"] == INVALID_PARAMS
    assert "archived" in response["error"]["message"]


async def test_merge_errors_when_stores_missing(tmp_path: Path) -> None:
    dispatcher, projects, _threads, _lifecycle, _ = await _setup(tmp_path, full=False)
    absorbed = await projects.create(name="UI")
    surviving = await projects.create(name="Thalyn")
    response = await _call(
        dispatcher,
        "project.merge",
        {
            "fromProjectId": absorbed.project_id,
            "intoProjectId": surviving.project_id,
        },
    )
    assert "error" in response
    assert response["error"]["code"] == INVALID_PARAMS
    assert "not configured" in response["error"]["message"]


async def test_merge_apply_writes_audit_file(tmp_path: Path) -> None:
    dispatcher, projects, threads, lifecycle, _ = await _setup(tmp_path)
    absorbed = await projects.create(name="UI")
    surviving = await projects.create(name="Thalyn")
    await lifecycle.spawn(SpawnRequest(project_id=absorbed.project_id))
    await lifecycle.spawn(SpawnRequest(project_id=surviving.project_id))
    thread = await _seed_thread(threads)
    await _seed_turn(threads, thread, absorbed.project_id, "ui")

    response = await _call(
        dispatcher,
        "project.merge",
        {
            "fromProjectId": absorbed.project_id,
            "intoProjectId": surviving.project_id,
            "apply": True,
        },
    )
    log_path = Path(response["result"]["outcome"]["logPath"])
    assert log_path.exists()
    assert log_path.parent.name == "merges"
