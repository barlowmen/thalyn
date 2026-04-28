"""JSON-RPC bindings for the eternal-thread surface.

Per ADR-0022 the surface is split between read-only methods that ship
in this stage (``thread.recent``, ``thread.search``, ``digest.latest``)
and orchestration-coupled methods (``thread.send``, ``digest.run``)
that the summarizer + send-path commits land alongside their backing
runtime. Handlers are stateless wrappers over the ``ThreadsStore``;
parameter validation matches the v1 IPC conventions in
``memory_rpc.py``.
"""

from __future__ import annotations

from thalyn_brain.rpc import (
    INVALID_PARAMS,
    Dispatcher,
    JsonValue,
    RpcError,
    RpcParams,
)
from thalyn_brain.threads import ThreadsStore

DEFAULT_RECENT_LIMIT = 50
MAX_RECENT_LIMIT = 200
DEFAULT_SEARCH_LIMIT = 10
MAX_SEARCH_LIMIT = 50


def register_thread_methods(dispatcher: Dispatcher, store: ThreadsStore) -> None:
    """Wire the read-side eternal-thread methods onto ``dispatcher``."""

    async def thread_recent(params: RpcParams) -> JsonValue:
        thread_id = _require_str(params, "threadId")
        limit = _validate_limit(params, "limit", DEFAULT_RECENT_LIMIT, MAX_RECENT_LIMIT)
        include_in_progress = bool(params.get("includeInProgress", False))
        turns = await store.list_recent(
            thread_id,
            limit=limit,
            include_in_progress=include_in_progress,
        )
        return {"threadId": thread_id, "turns": [t.to_wire() for t in turns]}

    async def thread_search(params: RpcParams) -> JsonValue:
        query = _require_str(params, "query")
        thread_id_value = params.get("threadId")
        thread_id = thread_id_value if isinstance(thread_id_value, str) else None
        limit = _validate_limit(params, "limit", DEFAULT_SEARCH_LIMIT, MAX_SEARCH_LIMIT)
        snippet = bool(params.get("snippet", True))
        try:
            hits = await store.search_turns(
                query,
                thread_id=thread_id,
                limit=limit,
                snippet=snippet,
            )
        except _SqliteError as exc:
            raise RpcError(code=INVALID_PARAMS, message=str(exc)) from exc
        return {
            "query": query,
            "threadId": thread_id,
            "hits": [h.to_wire() for h in hits],
        }

    async def digest_latest(params: RpcParams) -> JsonValue:
        thread_id = _require_str(params, "threadId")
        digest = await store.latest_digest(thread_id)
        return {
            "threadId": thread_id,
            "digest": digest.to_wire() if digest is not None else None,
        }

    dispatcher.register("thread.recent", thread_recent)
    dispatcher.register("thread.search", thread_search)
    dispatcher.register("digest.latest", digest_latest)


def _require_str(params: RpcParams, key: str) -> str:
    value = params.get(key)
    if not isinstance(value, str) or not value:
        raise RpcError(
            code=INVALID_PARAMS,
            message=f"missing or non-string '{key}'",
        )
    return value


def _validate_limit(
    params: RpcParams,
    key: str,
    default: int,
    maximum: int,
) -> int:
    raw = params.get(key, default)
    if not isinstance(raw, int) or raw <= 0:
        raise RpcError(
            code=INVALID_PARAMS,
            message=f"{key} must be a positive integer",
        )
    if raw > maximum:
        raise RpcError(
            code=INVALID_PARAMS,
            message=f"{key} may not exceed {maximum}",
        )
    return raw


# An import shim so the search handler can convert sqlite3 syntax errors
# (e.g. malformed FTS5 MATCH expression) into INVALID_PARAMS without
# importing sqlite3 at module top — keeps the public surface narrow.
import sqlite3 as _sqlite3  # noqa: E402

_SqliteError = _sqlite3.OperationalError
