"""JSON-RPC bindings for the lead lifecycle surface.

Five methods replace the v0.20 ``NOT_IMPLEMENTED`` stubs:

- ``lead.spawn``  — create a new lead for a project.
- ``lead.list``   — enumerate leads (filterable by project / status / kind).
- ``lead.pause``  — flip an active lead to paused.
- ``lead.resume`` — flip a paused lead back to active.
- ``lead.archive`` — retire a lead. Clears the project's lead pointer.

The handlers thin-wrap ``LeadLifecycle``: parse params, translate
``LeadLifecycleError`` into ``INVALID_PARAMS``, return wire-shape
agent records. The state machine itself owns the invariants.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from thalyn_brain.agents import AgentRecord
from thalyn_brain.lead_lifecycle import (
    LeadLifecycle,
    LeadLifecycleError,
    SpawnRequest,
)
from thalyn_brain.rpc import (
    INVALID_PARAMS,
    Dispatcher,
    JsonValue,
    RpcError,
    RpcParams,
)


def register_lead_methods(
    dispatcher: Dispatcher,
    lifecycle: LeadLifecycle,
) -> None:
    """Wire the ``lead.*`` methods onto ``dispatcher``."""

    async def lead_spawn(params: RpcParams) -> JsonValue:
        request = SpawnRequest(
            project_id=_require_str(params, "projectId"),
            display_name=_optional_str(params, "displayName"),
            default_provider_id=_optional_str(params, "defaultProviderId"),
            system_prompt=_optional_str(params, "systemPrompt"),
        )
        try:
            record = await lifecycle.spawn(request)
        except LeadLifecycleError as exc:
            raise RpcError(code=INVALID_PARAMS, message=str(exc)) from exc
        return {"agent": record.to_wire()}

    async def lead_list(params: RpcParams) -> JsonValue:
        try:
            records = await lifecycle.list_leads(
                project_id=_optional_str(params, "projectId"),
                status=_optional_str(params, "status"),
                kind=_optional_str(params, "kind"),
            )
        except LeadLifecycleError as exc:
            raise RpcError(code=INVALID_PARAMS, message=str(exc)) from exc
        return {"agents": [r.to_wire() for r in records]}

    async def lead_pause(params: RpcParams) -> JsonValue:
        return await _run_transition(params, lifecycle.pause)

    async def lead_resume(params: RpcParams) -> JsonValue:
        return await _run_transition(params, lifecycle.resume)

    async def lead_archive(params: RpcParams) -> JsonValue:
        return await _run_transition(params, lifecycle.archive)

    dispatcher.register("lead.spawn", lead_spawn)
    dispatcher.register("lead.list", lead_list)
    dispatcher.register("lead.pause", lead_pause)
    dispatcher.register("lead.resume", lead_resume)
    dispatcher.register("lead.archive", lead_archive)


async def _run_transition(
    params: RpcParams,
    transition: Callable[[str], Awaitable[AgentRecord]],
) -> JsonValue:
    agent_id = _require_str(params, "agentId")
    try:
        record = await transition(agent_id)
    except LeadLifecycleError as exc:
        raise RpcError(code=INVALID_PARAMS, message=str(exc)) from exc
    return {"agent": record.to_wire()}


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
