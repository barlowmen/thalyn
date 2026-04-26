"""JSON-RPC binding for the plan-approval gate.

The renderer calls ``run.approve_plan`` with a decision (approve /
edit / reject) and an optional edited plan; the brain resumes (or
kills) the run and streams the rest of the chunks the same way
chat.send does.
"""

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

VALID_DECISIONS = {"approve", "edit", "reject"}


def register_approval_methods(
    dispatcher: Dispatcher,
    runner: Runner,
) -> None:
    """Wire the run.approve_plan handler into the dispatcher."""

    async def approve_plan(params: RpcParams, notify: Notifier) -> JsonValue:
        run_id = _require_str(params, "runId")
        provider_id = _require_str(params, "providerId")
        decision = _require_str(params, "decision")
        if decision not in VALID_DECISIONS:
            raise RpcError(
                code=INVALID_PARAMS,
                message=f"decision must be one of {sorted(VALID_DECISIONS)}",
            )

        edited_plan = params.get("editedPlan")
        if decision == "edit":
            if not isinstance(edited_plan, dict):
                raise RpcError(
                    code=INVALID_PARAMS,
                    message="editedPlan is required when decision is 'edit'",
                )
        elif edited_plan is not None and not isinstance(edited_plan, dict):
            raise RpcError(
                code=INVALID_PARAMS,
                message="editedPlan must be an object when provided",
            )

        result = await runner.approve_plan(
            run_id=run_id,
            provider_id=provider_id,
            decision=decision,
            edited_plan=edited_plan if isinstance(edited_plan, dict) else None,
            notify=notify,
        )
        if result is None:
            raise RpcError(
                code=INVALID_PARAMS,
                message=f"no resumable run found for runId {run_id!r}",
            )

        summary: dict[str, Any] = {
            "runId": result.run_id,
            "sessionId": result.session_id,
            "providerId": result.provider_id,
            "status": result.status,
            "actionLogSize": result.action_log_size,
            "finalResponse": result.final_response,
        }
        if result.plan is not None:
            summary["plan"] = result.plan
        return summary

    dispatcher.register_streaming("run.approve_plan", approve_plan)


def _require_str(params: RpcParams, key: str) -> str:
    value = params.get(key)
    if not isinstance(value, str) or not value:
        raise RpcError(
            code=INVALID_PARAMS,
            message=f"missing or non-string '{key}'",
        )
    return value
