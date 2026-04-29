"""JSON-RPC bindings for the runs index."""

from __future__ import annotations

from typing import Any

from thalyn_brain.orchestration import Runner
from thalyn_brain.rpc import (
    INVALID_PARAMS,
    Dispatcher,
    JsonValue,
    Notifier,
    RpcError,
    RpcParams,
)
from thalyn_brain.runs import RunHeader, RunsStore


def register_runs_methods(
    dispatcher: Dispatcher,
    store: RunsStore,
    *,
    runner: Runner | None = None,
) -> None:
    async def runs_list(params: RpcParams) -> JsonValue:
        project_id = params.get("projectId")
        if project_id is not None and not isinstance(project_id, str):
            raise RpcError(code=INVALID_PARAMS, message="projectId must be a string")
        parent_lead_id = params.get("parentLeadId")
        if parent_lead_id is not None and not isinstance(parent_lead_id, str):
            raise RpcError(code=INVALID_PARAMS, message="parentLeadId must be a string")
        statuses = params.get("statuses")
        if statuses is not None and not (
            isinstance(statuses, list) and all(isinstance(s, str) for s in statuses)
        ):
            raise RpcError(code=INVALID_PARAMS, message="statuses must be a list of strings")
        limit_raw = params.get("limit", 100)
        if not isinstance(limit_raw, int) or limit_raw <= 0:
            raise RpcError(code=INVALID_PARAMS, message="limit must be a positive integer")
        rows = await store.list_runs(
            project_id=project_id,
            parent_lead_id=parent_lead_id,
            statuses=statuses,
            limit=limit_raw,
        )
        return {"runs": [row.to_wire() for row in rows]}

    async def runs_get(params: RpcParams) -> JsonValue:
        run_id = params.get("runId")
        if not isinstance(run_id, str) or not run_id:
            raise RpcError(code=INVALID_PARAMS, message="missing or non-string runId")
        header = await store.get(run_id)
        return _wire_or_none(header)

    async def runs_tree(params: RpcParams) -> JsonValue:
        run_id = params.get("runId")
        if not isinstance(run_id, str) or not run_id:
            raise RpcError(code=INVALID_PARAMS, message="missing or non-string runId")
        descendants = await store.list_descendants(run_id)
        if not descendants:
            return None
        return _build_tree(run_id, descendants)

    async def runs_kill(params: RpcParams, notify: Notifier) -> JsonValue:
        if runner is None:
            raise RpcError(code=INVALID_PARAMS, message="runs.kill is not wired in this dispatcher")
        run_id = params.get("runId")
        if not isinstance(run_id, str) or not run_id:
            raise RpcError(code=INVALID_PARAMS, message="missing or non-string runId")
        result = await runner.kill_run(run_id=run_id, notify=notify)
        if result is None:
            raise RpcError(code=INVALID_PARAMS, message=f"unknown runId: {run_id}")
        return {
            "runId": result.run_id,
            "status": result.status,
        }

    dispatcher.register("runs.list", runs_list)
    dispatcher.register("runs.get", runs_get)
    dispatcher.register("runs.tree", runs_tree)
    dispatcher.register_streaming("runs.kill", runs_kill)


def _wire_or_none(header: Any | None) -> dict[str, Any] | None:
    return header.to_wire() if header is not None else None


def _build_tree(root_run_id: str, headers: list[RunHeader]) -> dict[str, Any] | None:
    """Group ``headers`` by ``parent_run_id`` and return the root node."""
    by_id: dict[str, dict[str, Any]] = {}
    children_by_parent: dict[str, list[dict[str, Any]]] = {}

    for header in headers:
        node: dict[str, Any] = dict(header.to_wire())
        node["children"] = []
        by_id[header.run_id] = node
        if header.parent_run_id is not None:
            children_by_parent.setdefault(header.parent_run_id, []).append(node)

    for parent_id, kids in children_by_parent.items():
        parent_node = by_id.get(parent_id)
        if parent_node is None:
            continue
        # Stable order by start time so the renderer's tile list is
        # deterministic without a separate sort pass.
        kids.sort(key=lambda n: n.get("startedAtMs") or 0)
        parent_node["children"] = kids

    return by_id.get(root_run_id)
