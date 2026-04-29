"""Chat session lifecycle.

Each chat turn now flows through the LangGraph orchestrator
(`thalyn_brain.orchestration`). The handler routes the user prompt
through plan → execute → critic → respond and forwards the
graph's notifications (chat.chunk, run.plan_update, run.action_log,
run.status) on through the JSON-RPC dispatcher.

The wire shape stays compatible with v0.3 — `chat.send` is still the
entry point and still emits `chat.chunk` for streamed tokens — but
it gains the run-lifecycle vocabulary the renderer's inspector will
subscribe to next.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from thalyn_brain.orchestration import Runner
from thalyn_brain.orchestration.budget import Budget
from thalyn_brain.project_context import (
    load_project_context,
    merge_into_system_prompt,
)
from thalyn_brain.provider import ProviderNotImplementedError, ProviderRegistry
from thalyn_brain.rpc import (
    INVALID_PARAMS,
    Dispatcher,
    JsonValue,
    Notifier,
    RpcError,
    RpcParams,
)


def register_chat_methods(
    dispatcher: Dispatcher,
    registry: ProviderRegistry,
    *,
    runner: Runner | None = None,
) -> None:
    """Wire chat handlers into the dispatcher.

    A `Runner` may be passed in for tests that want to swap the
    checkpointer factory; production callers can omit it and use the
    default in-memory checkpointer.
    """
    bound_runner = runner or Runner(registry)

    async def chat_send(params: RpcParams, notify: Notifier) -> JsonValue:
        return await _handle_chat_send(params, notify, bound_runner)

    dispatcher.register_streaming("chat.send", chat_send)


async def _handle_chat_send(
    params: RpcParams,
    notify: Notifier,
    runner: Runner,
) -> JsonValue:
    session_id = _require_str(params, "sessionId")
    provider_id = _require_str(params, "providerId")
    prompt = _require_str(params, "prompt")
    budget = Budget.from_wire(params.get("budget"))

    base_system_prompt = params.get("systemPrompt")
    if base_system_prompt is not None and not isinstance(base_system_prompt, str):
        raise RpcError(
            code=INVALID_PARAMS,
            message="systemPrompt must be a string when provided",
        )

    lead_id_value = params.get("leadId")
    if lead_id_value is not None and not isinstance(lead_id_value, str):
        raise RpcError(
            code=INVALID_PARAMS,
            message="leadId must be a string when provided",
        )
    # When the caller names a lead, the run is *the lead's*: the run
    # header records ``agent_id=leadId`` (who's running), and every
    # spawned worker inherits ``parent_lead_id=leadId`` so a drill
    # by lead surfaces the whole tree (per ADR-0021).
    lead_id: str | None = lead_id_value or None

    workspace_root_value = params.get("workspaceRoot")
    project_context = None
    if isinstance(workspace_root_value, str) and workspace_root_value:
        project_context = load_project_context(Path(workspace_root_value))

    system_prompt = merge_into_system_prompt(base_system_prompt, project_context)

    try:
        result = await runner.run(
            session_id=session_id,
            provider_id=provider_id,
            prompt=prompt,
            notify=notify,
            budget=budget,
            system_prompt=system_prompt,
            agent_id=lead_id,
            parent_lead_id=lead_id,
        )
    except ProviderNotImplementedError as exc:
        raise RpcError(code=INVALID_PARAMS, message=str(exc)) from exc

    summary: dict[str, Any] = {
        "sessionId": result.session_id,
        "providerId": result.provider_id,
        "runId": result.run_id,
        "status": result.status,
        "actionLogSize": result.action_log_size,
    }
    if result.plan is not None:
        summary["plan"] = result.plan
    if project_context is not None:
        summary["projectContext"] = project_context.to_wire()
    if lead_id is not None:
        summary["leadId"] = lead_id
    return summary


def _require_str(params: RpcParams, key: str) -> str:
    value = params.get(key)
    if not isinstance(value, str) or not value:
        raise RpcError(
            code=INVALID_PARAMS,
            message=f"missing or non-string '{key}'",
        )
    return value
