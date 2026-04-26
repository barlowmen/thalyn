"""JSON-RPC bindings for the runs index."""

from __future__ import annotations

from typing import Any

from thalyn_brain.rpc import (
    INVALID_PARAMS,
    Dispatcher,
    JsonValue,
    RpcError,
    RpcParams,
)
from thalyn_brain.runs import RunsStore


def register_runs_methods(dispatcher: Dispatcher, store: RunsStore) -> None:
    async def runs_list(params: RpcParams) -> JsonValue:
        project_id = params.get("projectId")
        if project_id is not None and not isinstance(project_id, str):
            raise RpcError(code=INVALID_PARAMS, message="projectId must be a string")
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

    dispatcher.register("runs.list", runs_list)
    dispatcher.register("runs.get", runs_get)


def _wire_or_none(header: Any | None) -> dict[str, Any] | None:
    return header.to_wire() if header is not None else None
