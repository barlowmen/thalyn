"""JSON-RPC bindings for the memory store."""

from __future__ import annotations

import time

from thalyn_brain.memory import (
    MEMORY_KINDS,
    MEMORY_SCOPES,
    MemoryEntry,
    MemoryStore,
    MemoryUpdate,
    new_memory_id,
)
from thalyn_brain.rpc import (
    INVALID_PARAMS,
    Dispatcher,
    JsonValue,
    RpcError,
    RpcParams,
)


def register_memory_methods(dispatcher: Dispatcher, store: MemoryStore) -> None:
    async def memory_list(params: RpcParams) -> JsonValue:
        project_id = params.get("projectId")
        if project_id is not None and not isinstance(project_id, str):
            raise RpcError(code=INVALID_PARAMS, message="projectId must be a string")
        scopes = params.get("scopes")
        if scopes is not None:
            if not (isinstance(scopes, list) and all(isinstance(s, str) for s in scopes)):
                raise RpcError(
                    code=INVALID_PARAMS,
                    message="scopes must be a list of strings",
                )
        limit_raw = params.get("limit", 200)
        if not isinstance(limit_raw, int) or limit_raw <= 0:
            raise RpcError(code=INVALID_PARAMS, message="limit must be a positive integer")
        rows = await store.list_entries(
            project_id=project_id,
            scopes=scopes,
            limit=limit_raw,
        )
        return {"entries": [row.to_wire() for row in rows]}

    async def memory_search(params: RpcParams) -> JsonValue:
        query = _require_str(params, "query")
        project_id = params.get("projectId")
        if project_id is not None and not isinstance(project_id, str):
            raise RpcError(code=INVALID_PARAMS, message="projectId must be a string")
        limit_raw = params.get("limit", 20)
        if not isinstance(limit_raw, int) or limit_raw <= 0:
            raise RpcError(code=INVALID_PARAMS, message="limit must be a positive integer")
        rows = await store.search(query, project_id=project_id, limit=limit_raw)
        return {"entries": [row.to_wire() for row in rows]}

    async def memory_add(params: RpcParams) -> JsonValue:
        body = _require_str(params, "body")
        scope = _validate_choice(params, "scope", MEMORY_SCOPES)
        kind = _validate_choice(params, "kind", MEMORY_KINDS)
        author = _require_str(params, "author")
        project_id_value = params.get("projectId")
        project_id = project_id_value if isinstance(project_id_value, str) else None

        now_ms = int(time.time() * 1000)
        entry = MemoryEntry(
            memory_id=new_memory_id(),
            project_id=project_id,
            scope=scope,
            kind=kind,
            body=body,
            author=author,
            created_at_ms=now_ms,
            updated_at_ms=now_ms,
        )
        await store.insert(entry)
        return {"entry": entry.to_wire()}

    async def memory_update(params: RpcParams) -> JsonValue:
        memory_id = _require_str(params, "memoryId")
        update = MemoryUpdate()
        if "body" in params:
            body = params["body"]
            if not isinstance(body, str):
                raise RpcError(code=INVALID_PARAMS, message="body must be a string")
            update.with_body(body)
        if "kind" in params:
            kind = params["kind"]
            if not isinstance(kind, str) or kind not in MEMORY_KINDS:
                raise RpcError(
                    code=INVALID_PARAMS,
                    message=f"kind must be one of {sorted(MEMORY_KINDS)}",
                )
            update.with_kind(kind)
        if "scope" in params:
            scope = params["scope"]
            if not isinstance(scope, str) or scope not in MEMORY_SCOPES:
                raise RpcError(
                    code=INVALID_PARAMS,
                    message=f"scope must be one of {sorted(MEMORY_SCOPES)}",
                )
            update.with_scope(scope)

        updated = await store.update(memory_id, update)
        if not updated:
            raise RpcError(code=INVALID_PARAMS, message=f"unknown memoryId: {memory_id}")
        entry = await store.get(memory_id)
        return {"entry": entry.to_wire() if entry else None}

    async def memory_delete(params: RpcParams) -> JsonValue:
        memory_id = _require_str(params, "memoryId")
        deleted = await store.delete(memory_id)
        return {"deleted": deleted, "memoryId": memory_id}

    dispatcher.register("memory.list", memory_list)
    dispatcher.register("memory.search", memory_search)
    dispatcher.register("memory.add", memory_add)
    dispatcher.register("memory.update", memory_update)
    dispatcher.register("memory.delete", memory_delete)


def _require_str(params: RpcParams, key: str) -> str:
    value = params.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RpcError(code=INVALID_PARAMS, message=f"missing or non-string {key!r}")
    return value


def _validate_choice(params: RpcParams, key: str, choices: frozenset[str]) -> str:
    value = params.get(key)
    if not isinstance(value, str) or value not in choices:
        raise RpcError(
            code=INVALID_PARAMS,
            message=f"{key} must be one of {sorted(choices)}",
        )
    return value
