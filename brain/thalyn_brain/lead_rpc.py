"""JSON-RPC bindings for the lead lifecycle surface.

Six methods cover the v2 lead lifecycle:

- ``lead.spawn``           — create a new top-level lead for a project.
- ``lead.spawn_sub_lead``  — spawn a sub-lead under an existing
  active lead (F2.3 / Phase v0.36). Surfaces a ``code: -32099``
  ``DEPTH_CAP_EXCEEDED`` error with the parent + attempted depth in
  ``data`` so the renderer can stage a ``gateKind: "depth"`` approval
  rather than treating it as an input error.
- ``lead.list``            — enumerate leads + sub-leads.
- ``lead.pause``           — flip an active lead to paused.
- ``lead.resume``          — flip a paused lead back to active.
- ``lead.archive``         — retire a lead. Clears the project's lead
  pointer when the archived agent was a top-level lead.

The handlers thin-wrap ``LeadLifecycle``: parse params, translate
``LeadLifecycleError`` into ``INVALID_PARAMS``, return wire-shape
agent records. The state machine itself owns the invariants.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from thalyn_brain.agents import AgentRecord
from thalyn_brain.lead_lifecycle import (
    DEPTH_CAP,
    DepthCapExceededError,
    LeadLifecycle,
    LeadLifecycleError,
    SpawnRequest,
    SubLeadSpawnRequest,
)
from thalyn_brain.rpc import (
    INVALID_PARAMS,
    Dispatcher,
    JsonValue,
    RpcError,
    RpcParams,
)

# Custom JSON-RPC code for the depth-cap path. Sits in the
# implementation-defined server-error range (-32099 .. -32000) so it
# doesn't collide with the reserved JSON-RPC codes; matches the
# ``NOT_IMPLEMENTED`` pattern other v2 surfaces use.
DEPTH_CAP_EXCEEDED = -32099


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

    async def lead_spawn_sub_lead(params: RpcParams) -> JsonValue:
        request = SubLeadSpawnRequest(
            parent_agent_id=_require_str(params, "parentAgentId"),
            scope_facet=_require_str(params, "scopeFacet"),
            display_name=_optional_str(params, "displayName"),
            default_provider_id=_optional_str(params, "defaultProviderId"),
            system_prompt=_optional_str(params, "systemPrompt"),
            override_depth_cap=_optional_bool(params, "overrideDepthCap"),
        )
        try:
            record = await lifecycle.spawn_sub_lead(request)
        except DepthCapExceededError as exc:
            # The depth-cap path is qualitatively different from a
            # validation error: the user (or Thalyn) needs to stage a
            # ``gateKind: "depth"`` approval and re-call with
            # ``overrideDepthCap=true``. Surface a custom code with
            # the structured detail in ``data`` so the renderer can
            # branch without parsing the message.
            raise RpcError(
                code=DEPTH_CAP_EXCEEDED,
                message=str(exc),
                data={
                    "parentAgentId": exc.parent_agent_id,
                    "attemptedDepth": exc.attempted_depth,
                    "depthCap": DEPTH_CAP,
                },
            ) from exc
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
    dispatcher.register("lead.spawn_sub_lead", lead_spawn_sub_lead)
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


def _optional_bool(params: RpcParams, key: str) -> bool:
    if key not in params or params[key] is None:
        return False
    value = params[key]
    if not isinstance(value, bool):
        raise RpcError(
            code=INVALID_PARAMS,
            message=f"'{key}' must be a boolean when present",
        )
    return value
