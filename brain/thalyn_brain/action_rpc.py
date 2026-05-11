"""JSON-RPC bindings for the action registry.

Exposes four methods over the brain's stdio surface:

- ``action.list`` — names + descriptions of every registered
  configurable surface. The renderer reads this to populate the
  command-palette / discovery surface; Thalyn reads it as part of
  his per-turn context so he knows what surfaces exist.
- ``action.describe`` — full input schema for one action. Pulled on
  demand when Thalyn or the renderer needs to walk the user through
  inputs.
- ``action.approve`` — resolve a hard-gated pending action and run
  the executor. Returns the executor's confirmation + followup
  payload.
- ``action.reject`` — drop a pending action without running it.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from thalyn_brain.action_registry import (
    ActionRegistry,
    ActionRegistryError,
    UnknownActionError,
)
from thalyn_brain.pending_actions import APPROVED, REJECTED, PendingActionStore
from thalyn_brain.rpc import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    Dispatcher,
    JsonValue,
    RpcError,
    RpcParams,
)


def register_action_methods(
    dispatcher: Dispatcher,
    *,
    registry: ActionRegistry,
    pending_actions: PendingActionStore,
) -> None:
    async def action_list(_: RpcParams) -> JsonValue:
        summaries = registry.list_summaries()
        return {
            "actions": [
                {
                    "name": s.name,
                    "description": s.description,
                    "hardGate": s.hard_gate,
                }
                for s in summaries
            ],
        }

    async def action_describe(params: RpcParams) -> JsonValue:
        name = _require_str(params, "name")
        try:
            return registry.describe(name)
        except UnknownActionError as exc:
            raise RpcError(code=INVALID_PARAMS, message=str(exc)) from exc

    async def action_approve(params: RpcParams) -> JsonValue:
        approval_id = _require_str(params, "approvalId")
        entry = await pending_actions.get(approval_id)
        if entry is None:
            raise RpcError(
                code=INVALID_PARAMS,
                message=f"unknown approval id: {approval_id}",
            )
        if entry.status != "pending":
            raise RpcError(
                code=INVALID_PARAMS,
                message=(f"approval {approval_id!r} is already resolved ({entry.status})"),
            )
        resolved = await pending_actions.resolve(approval_id, status=APPROVED)
        if resolved is None:
            # Lost a race against another concurrent resolver.
            raise RpcError(
                code=INVALID_PARAMS,
                message=f"approval {approval_id!r} was resolved concurrently",
            )
        try:
            result = await registry.execute(
                resolved.action_name,
                resolved.inputs,
                hard_gate_resolved=True,
            )
        except ActionRegistryError as exc:
            raise RpcError(code=INTERNAL_ERROR, message=str(exc)) from exc
        return _approval_result_to_wire(
            resolved,
            confirmation=result.confirmation,
            followup=result.followup,
        )

    async def action_reject(params: RpcParams) -> JsonValue:
        approval_id = _require_str(params, "approvalId")
        resolved = await pending_actions.resolve(approval_id, status=REJECTED)
        if resolved is None:
            raise RpcError(
                code=INVALID_PARAMS,
                message=f"approval {approval_id!r} is unknown or already resolved",
            )
        return {
            "approvalId": approval_id,
            "actionName": resolved.action_name,
            "status": REJECTED,
            "resolvedAtMs": resolved.resolved_at_ms,
        }

    async def action_list_pending(_: RpcParams) -> JsonValue:
        pending = await pending_actions.list_pending()
        return {"pending": [entry.to_wire() for entry in pending]}

    dispatcher.register("action.list", action_list)
    dispatcher.register("action.describe", action_describe)
    dispatcher.register("action.approve", action_approve)
    dispatcher.register("action.reject", action_reject)
    dispatcher.register("action.list_pending", action_list_pending)


def _approval_result_to_wire(
    resolved: Any,
    *,
    confirmation: str,
    followup: Mapping[str, Any] | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "approvalId": resolved.approval_id,
        "actionName": resolved.action_name,
        "status": resolved.status,
        "resolvedAtMs": resolved.resolved_at_ms,
        "confirmation": confirmation,
    }
    if followup is not None:
        payload["followup"] = dict(followup)
    return payload


def _require_str(params: RpcParams, key: str) -> str:
    value = params.get(key)
    if not isinstance(value, str) or not value:
        raise RpcError(
            code=INVALID_PARAMS,
            message=f"missing or non-string {key!r}",
        )
    return value


__all__ = ["register_action_methods"]
