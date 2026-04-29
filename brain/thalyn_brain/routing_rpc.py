"""JSON-RPC bindings for the worker routing surface.

Replaces the v2 ``NOT_IMPLEMENTED`` stubs with real handlers that drive
``RoutingOverridesStore`` and resolve effective routes through the pure
``route_worker`` function:

- ``routing.get``    — resolve the effective route for ``(taskTag, projectId)``
                       using the current overrides + project privacy flag.
                       Useful for inspector / sanity views.
- ``routing.set``    — upsert a project override for one tag.
- ``routing.clear``  — delete a project override for one tag.
- ``routing.list``   — enumerate stored overrides for one project (the
                       inspector view; not in the v2 stubs but cheap to
                       expose because the store already has the query).

The handlers are thin wrappers: parse params, call the store / route
function, return wire-shape dicts. Validation lives here (string types,
required keys, recognized provider ids); the privacy invariant
(``local_only``) and the resolution-order matrix live in
``routing_table``.
"""

from __future__ import annotations

import time
from collections.abc import Iterable

from thalyn_brain.projects import ProjectsStore
from thalyn_brain.routing import (
    RoutingOverride,
    RoutingOverridesStore,
    new_routing_override_id,
)
from thalyn_brain.routing_table import (
    DEFAULT_GLOBAL_DEFAULTS,
    RouteDecision,
    route_worker,
)
from thalyn_brain.rpc import (
    INVALID_PARAMS,
    Dispatcher,
    JsonValue,
    RpcError,
    RpcParams,
)


def register_routing_methods(
    dispatcher: Dispatcher,
    *,
    overrides_store: RoutingOverridesStore,
    projects_store: ProjectsStore,
    valid_provider_ids: Iterable[str] | None = None,
) -> None:
    """Wire ``routing.*`` handlers onto ``dispatcher``.

    ``valid_provider_ids`` lets the caller restrict ``routing.set`` to
    known provider registry keys; passing ``None`` disables the check
    (tests can opt out without wiring a registry).
    """
    allowed_providers = frozenset(valid_provider_ids) if valid_provider_ids else None

    async def routing_get(params: RpcParams) -> JsonValue:
        project_id = _require_str(params, "projectId")
        task_tag = _optional_str(params, "taskTag")
        decision = await _resolve_decision(
            overrides_store=overrides_store,
            projects_store=projects_store,
            project_id=project_id,
            task_tag=task_tag,
        )
        return _decision_to_wire(decision, project_id=project_id)

    async def routing_set(params: RpcParams) -> JsonValue:
        project_id = _require_str(params, "projectId")
        task_tag = _require_str(params, "taskTag")
        provider_id = _require_str(params, "providerId")
        if allowed_providers is not None and provider_id not in allowed_providers:
            raise RpcError(
                code=INVALID_PARAMS,
                message=f"unknown providerId: {provider_id}",
            )
        # The route function normalizes; the store keeps the user's
        # raw tag value as-is so the override row mirrors what the
        # user typed (no surprise rewrites in the inspector).
        override = RoutingOverride(
            routing_override_id=new_routing_override_id(),
            project_id=project_id,
            task_tag=task_tag,
            provider_id=provider_id,
            updated_at_ms=int(time.time() * 1000),
        )
        await overrides_store.upsert(override)
        return {"override": override.to_wire()}

    async def routing_clear(params: RpcParams) -> JsonValue:
        project_id = _require_str(params, "projectId")
        task_tag = _require_str(params, "taskTag")
        cleared = await overrides_store.delete(project_id, task_tag)
        return {"cleared": cleared}

    async def routing_list(params: RpcParams) -> JsonValue:
        project_id = _require_str(params, "projectId")
        overrides = await overrides_store.list_for_project(project_id)
        return {"overrides": [o.to_wire() for o in overrides]}

    dispatcher.register("routing.get", routing_get)
    dispatcher.register("routing.set", routing_set)
    dispatcher.register("routing.clear", routing_clear)
    dispatcher.register("routing.list", routing_list)


async def _resolve_decision(
    *,
    overrides_store: RoutingOverridesStore,
    projects_store: ProjectsStore,
    project_id: str,
    task_tag: str | None,
) -> RouteDecision:
    """Load overrides + project flag and run the pure resolver.

    Used by the IPC handler and by the spawn path so the inspector
    preview and the live decision agree. The project lookup is
    optional — a missing project is treated as ``local_only=False``
    with no overrides so the IPC surface still answers cleanly
    (``routing.get`` can be called for a project that hasn't been
    created yet, e.g. from a setup wizard preview).
    """
    overrides_rows = await overrides_store.list_for_project(project_id)
    overrides = {row.task_tag: row.provider_id for row in overrides_rows}

    project = await projects_store.get(project_id)
    local_only = bool(project.local_only) if project is not None else False

    return route_worker(
        task_tag=task_tag,
        project_overrides=overrides,
        project_local_only=local_only,
        global_defaults=DEFAULT_GLOBAL_DEFAULTS,
    )


def _decision_to_wire(decision: RouteDecision, *, project_id: str) -> JsonValue:
    return {
        "projectId": project_id,
        "taskTag": decision.task_tag,
        "effectiveTag": decision.effective_tag,
        "providerId": decision.provider_id,
        "matched": decision.matched.value,
    }


def _require_str(params: RpcParams, key: str) -> str:
    value = params.get(key)
    if not isinstance(value, str) or not value:
        raise RpcError(
            code=INVALID_PARAMS,
            message=f"missing or non-string '{key}'",
        )
    return value


def _optional_str(params: RpcParams, key: str) -> str | None:
    if key not in params or params[key] is None:
        return None
    value = params[key]
    if not isinstance(value, str):
        raise RpcError(
            code=INVALID_PARAMS,
            message=f"'{key}' must be a string when present",
        )
    return value or None
