"""LangGraph wiring for the brain orchestrator.

The graph runs ``plan → execute → critic → respond`` sequentially.
Plan asks the active provider for a structured plan; critic is a
pass-through gate until the drift / budget logic lands; execute
dispatches a sub-agent for any plan step that requested one and
otherwise stays a structural transition. Respond streams the final
user-visible turn.

A ``Notifier`` is threaded through every node so they can announce
plan updates, action-log entries, and status transitions to the IPC
client mid-flight without coupling the orchestrator to the transport.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from langgraph.graph import END, START, StateGraph

from thalyn_brain.orchestration.budget import (
    Budget,
    BudgetConsumption,
    check_budget,
    estimate_tokens_from_text,
)
from thalyn_brain.orchestration.critic import (
    DEFAULT_DRIFT_PAUSE_THRESHOLD,
    crossed_thresholds,
    run_critic_checkpoint,
)
from thalyn_brain.orchestration.drift import combined_drift
from thalyn_brain.orchestration.planner import plan_for
from thalyn_brain.orchestration.state import (
    ActionLogEntry,
    GraphState,
    RunStatus,
)
from thalyn_brain.orchestration.subagent import SubAgentSpawner
from thalyn_brain.provider import (
    ChatChunk,
    ChatErrorChunk,
    ChatStartChunk,
    ChatStopChunk,
    ChatTextChunk,
    ChatToolCallChunk,
    ChatToolResultChunk,
    LlmProvider,
)

# Notifier type — same shape as the JSON-RPC dispatcher's Notifier
# alias, deliberately not imported from there so the orchestrator can
# stay decoupled from the wire transport.
Notifier = Callable[[str, Any], Awaitable[None]]


# Notification method names — mirrors the architecture's `run.*`
# vocabulary. Chat token chunks reuse the existing chat.chunk method
# the renderer already subscribes to.
RUN_PLAN_UPDATE = "run.plan_update"
RUN_ACTION_LOG = "run.action_log"
RUN_STATUS = "run.status"
RUN_APPROVAL_REQUIRED = "run.approval_required"
CHAT_CHUNK = "chat.chunk"


def _now_ms() -> int:
    return int(time.time() * 1000)


async def _enter_node(
    state: GraphState,
    *,
    node_name: str,
    notify: Notifier,
    extra_tokens: int = 0,
) -> tuple[BudgetConsumption, dict[str, Any] | None]:
    """Bookkeeping every node performs at the start: bump iteration
    count, fold any caller-supplied token estimate in, and consult
    the budget. Returns the freshly-updated consumption and ``None``
    on success — or a halt-state dict the caller should return as-is
    when the budget is exceeded.
    """
    consumed = BudgetConsumption.from_wire(state.get("budget_consumed") or {})
    consumed = consumed.with_iteration().with_tokens(extra_tokens).refresh_elapsed()

    log = await _record_budget_check(state, consumed, node_name=node_name, notify=notify)

    halt = await _halt_if_over_budget(state, consumed, action_log=log, notify=notify)
    return consumed, halt


async def _maybe_halt_after_work(
    state: GraphState,
    consumed: BudgetConsumption,
    *,
    node_name: str,
    notify: Notifier,
) -> dict[str, Any] | None:
    """Re-check budget after a node has done token-consuming work.

    Used by plan and respond, which charge significant tokens after
    the entry check. Records a fresh `drift_check` audit entry so the
    log captures the post-work consumption regardless of whether the
    cap trips.
    """
    refreshed = consumed.refresh_elapsed()
    log = await _record_budget_check(state, refreshed, node_name=f"{node_name}_post", notify=notify)
    return await _halt_if_over_budget(state, refreshed, action_log=log, notify=notify)


async def _record_budget_check(
    state: GraphState,
    consumed: BudgetConsumption,
    *,
    node_name: str,
    notify: Notifier,
) -> list[dict[str, Any]]:
    budget = Budget.from_wire(state.get("budget"))
    check = check_budget(budget, consumed)
    audit_entry = ActionLogEntry(
        at_ms=_now_ms(),
        kind="drift_check",
        payload={
            "step": "budget_check",
            "node": node_name,
            "exceeded": check.exceeded,
            "dimension": check.dimension,
            "iterations": consumed.iterations,
            "tokensUsed": consumed.tokens_used,
            "elapsedSeconds": consumed.elapsed_seconds,
        },
    )
    await _emit_action(state, audit_entry, notify)
    return _append_log(state, audit_entry)


async def _halt_if_over_budget(
    state: GraphState,
    consumed: BudgetConsumption,
    *,
    action_log: list[dict[str, Any]],
    notify: Notifier,
) -> dict[str, Any] | None:
    budget = Budget.from_wire(state.get("budget"))
    check = check_budget(budget, consumed)
    if not check.exceeded:
        return None

    await notify(
        RUN_APPROVAL_REQUIRED,
        {
            "runId": state["run_id"],
            "gateKind": "budget",
            "dimension": check.dimension,
            "limit": check.limit,
            "actual": check.actual,
            "reason": check.reason,
        },
    )
    await notify(
        RUN_STATUS,
        {
            "runId": state["run_id"],
            "status": RunStatus.KILLED.value,
            "parentRunId": state.get("parent_run_id"),
        },
    )
    return {
        "status": RunStatus.KILLED.value,
        "error": check.reason,
        "budget_consumed": consumed.to_wire(),
        "action_log": action_log,
    }


def _is_halted(state: GraphState) -> bool:
    """A node returning early when an upstream budget gate fired."""
    status = state.get("status")
    return status == RunStatus.KILLED.value or status == RunStatus.ERRORED.value


def build_graph(
    provider: LlmProvider,
    notify: Notifier,
    *,
    checkpointer: Any | None = None,
    interrupt_on_plan_approval: bool = True,
    spawn_subagent: SubAgentSpawner | None = None,
) -> Any:
    """Build and compile the brain graph.

    The provider supplies LLM traffic; `notify` is the side-channel
    nodes use to push live updates to the renderer; `checkpointer`
    persists graph state across the interrupt and across app restarts.

    When ``interrupt_on_plan_approval`` is set the graph pauses
    before the ``execute`` node so the user can approve, edit, or
    reject the plan. Tests that don't care about the interrupt can
    flip the flag off and let the graph run end-to-end.

    ``spawn_subagent`` is the runner-supplied callback the execute
    node uses to dispatch a focused worker for any plan step that
    asks for one. Leaving it ``None`` keeps execute as a structural
    pass-through, which is the right default for sub-agents
    themselves and for tests that don't exercise the spawn path.
    """
    graph: StateGraph[GraphState] = StateGraph(GraphState)
    # LangGraph's add_node overloads don't enjoy our explicit
    # Callable signature on the closure factories. The runtime
    # contract is satisfied — we ignore the typed surface here.
    graph.add_node("plan", _plan_node(provider, notify))  # type: ignore[call-overload]
    graph.add_node(
        "execute",
        _execute_node(notify, spawn_subagent),  # type: ignore[call-overload]
    )
    graph.add_node("critic", _critic_node(provider, notify))  # type: ignore[call-overload]
    graph.add_node("respond", _respond_node(provider, notify))  # type: ignore[call-overload]

    graph.add_edge(START, "plan")
    graph.add_edge("plan", "execute")
    graph.add_edge("execute", "critic")
    graph.add_edge("critic", "respond")
    graph.add_edge("respond", END)

    interrupt_before = ["execute"] if interrupt_on_plan_approval else []
    return graph.compile(
        checkpointer=checkpointer,
        interrupt_before=interrupt_before,
    )


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


def _plan_node(
    provider: LlmProvider,
    notify: Notifier,
) -> Callable[[GraphState], Awaitable[dict[str, Any]]]:
    """Build the plan node closure.

    The planner asks the active provider for a structured plan and
    surfaces it as a `Plan` tree. The fallback path inside the planner
    guarantees we always emit at least a single-step plan even if the
    model declines to decompose or the JSON fails to parse.
    """

    async def node(state: GraphState) -> dict[str, Any]:
        if _is_halted(state):
            return {}
        consumed, halt = await _enter_node(state, node_name="plan", notify=notify)
        if halt is not None:
            return halt

        await _emit_status(state, RunStatus.PLANNING, notify)

        result = await plan_for(provider, state["user_message"])
        plan_wire = result.plan.to_wire()
        await notify(
            RUN_PLAN_UPDATE,
            {"runId": state["run_id"], "plan": plan_wire},
        )

        # Fold the planner's raw text length into the token budget so
        # long plans count against the cap.
        consumed = consumed.with_tokens(estimate_tokens_from_text(result.raw_text))

        entry = ActionLogEntry(
            at_ms=_now_ms(),
            kind="decision",
            payload={
                "step": "plan",
                "nodeCount": len(result.plan.nodes),
                "rawTextLength": len(result.raw_text),
            },
        )
        await _emit_action(state, entry, notify)
        action_log = _append_log(state, entry)

        # Re-check budget now that the planner's text is folded in;
        # the entry check ran before the LLM call.
        intermediate_state: GraphState = {**state, "action_log": action_log}
        post_halt = await _maybe_halt_after_work(
            intermediate_state, consumed, node_name="plan", notify=notify
        )
        if post_halt is not None:
            return post_halt

        return {
            "plan": plan_wire,
            "action_log": action_log,
            "budget_consumed": consumed.to_wire(),
        }

    return node


def _execute_node(
    notify: Notifier,
    spawn_subagent: SubAgentSpawner | None,
) -> Callable[[GraphState], Awaitable[dict[str, Any]]]:
    """Run the plan.

    Plan steps that carry a ``subagentKind`` are dispatched to the
    runner's spawner; the resulting child run drives its own graph
    against its own checkpoint and surfaces lifecycle events under
    its own ``runId``. Steps without a kind stay inline — execute
    simply records the boundary and lets respond pick up from there.
    """

    async def node(state: GraphState) -> dict[str, Any]:
        if _is_halted(state):
            return {}
        consumed, halt = await _enter_node(state, node_name="execute", notify=notify)
        if halt is not None:
            return halt

        await _emit_status(state, RunStatus.RUNNING, notify)
        entry = ActionLogEntry(
            at_ms=_now_ms(),
            kind="node_transition",
            payload={"from": "plan", "to": "execute"},
        )
        await _emit_action(state, entry, notify)
        action_log = _append_log(state, entry)
        state = {**state, "action_log": action_log, "budget_consumed": consumed.to_wire()}

        plan = state.get("plan") or {}
        nodes_wire = plan.get("nodes") if isinstance(plan, dict) else None
        results: list[dict[str, Any]] = []
        if spawn_subagent is not None and isinstance(nodes_wire, list) and nodes_wire:
            depth = int(state.get("depth", 0))
            for plan_node in nodes_wire:
                if not isinstance(plan_node, dict):
                    continue
                if not plan_node.get("subagentKind"):
                    continue
                spawn_entry = ActionLogEntry(
                    at_ms=_now_ms(),
                    kind="decision",
                    payload={
                        "step": "spawn_subagent",
                        "planNodeId": plan_node.get("id"),
                        "subagentKind": plan_node.get("subagentKind"),
                    },
                )
                await _emit_action(state, spawn_entry, notify)
                action_log = _append_log(state, spawn_entry)
                state = {**state, "action_log": action_log}

                spawn_result = await spawn_subagent(
                    parent_run_id=state["run_id"],
                    plan_node=plan_node,
                    depth=depth,
                )
                wire = spawn_result.to_wire()
                results.append(wire)

                done_entry = ActionLogEntry(
                    at_ms=_now_ms(),
                    kind="decision",
                    payload={
                        "step": "subagent_completed",
                        "planNodeId": wire["planNodeId"],
                        "childRunId": wire["childRunId"],
                        "status": wire["status"],
                    },
                )
                await _emit_action(state, done_entry, notify)
                action_log = _append_log(state, done_entry)
                state = {**state, "action_log": action_log}

        return {
            "action_log": action_log,
            "subagent_results": results,
            "budget_consumed": consumed.to_wire(),
        }

    return node


def _critic_node(
    provider: LlmProvider,
    notify: Notifier,
) -> Callable[[GraphState], Awaitable[dict[str, Any]]]:
    """Critic gate.

    Two responsibilities here:

    1. Budget enforcement (inherited from the entry check) — a node
       transition counts as one iteration.
    2. Drift monitoring at 25 / 50 / 75 % budget consumption. For
       each newly-crossed threshold we drive a critic LLM round-trip
       and record the result in the action log. A drift score above
       the pause threshold halts the run pending review (the resume
       wire surface lands in a follow-up commit).
    """

    async def node(state: GraphState) -> dict[str, Any]:
        if _is_halted(state):
            return {}
        consumed, halt = await _enter_node(state, node_name="critic", notify=notify)
        if halt is not None:
            return halt

        entry = ActionLogEntry(
            at_ms=_now_ms(),
            kind="node_transition",
            payload={"from": "execute", "to": "critic"},
        )
        await _emit_action(state, entry, notify)
        action_log = _append_log(state, entry)

        already_hit = list(state.get("critic_thresholds_hit") or [])
        new_thresholds = crossed_thresholds(
            consumed.to_wire(),
            state.get("budget"),
            already_hit=already_hit,
        )
        latest_drift = float(state.get("drift_score") or 0.0)
        for threshold_label in new_thresholds:
            report = await run_critic_checkpoint(
                provider,
                user_message=state.get("user_message", ""),
                plan=state.get("plan"),
                action_log=action_log,
                threshold_label=threshold_label,
            )
            # The reported drift score combines the LLM verdict with a
            # local heuristic over plan-node ↔ action-log adherence;
            # either signal can independently push the run into the
            # pause-pending zone.
            combined_score = combined_drift(report.drift_score, state.get("plan"), action_log)
            latest_drift = combined_score
            already_hit.append(threshold_label)
            critic_entry = ActionLogEntry(
                at_ms=_now_ms(),
                kind="drift_check",
                payload={
                    "step": "critic",
                    "threshold": threshold_label,
                    "driftScore": combined_score,
                    "criticScore": report.drift_score,
                    "onTrack": report.on_track,
                    "reason": report.reason,
                },
            )
            await _emit_action(state, critic_entry, notify)
            action_log = _append_log({**state, "action_log": action_log}, critic_entry)

            if combined_score >= DEFAULT_DRIFT_PAUSE_THRESHOLD:
                await notify(
                    RUN_APPROVAL_REQUIRED,
                    {
                        "runId": state["run_id"],
                        "gateKind": "drift",
                        "threshold": threshold_label,
                        "driftScore": combined_score,
                        "reason": report.reason,
                    },
                )
                await notify(
                    RUN_STATUS,
                    {
                        "runId": state["run_id"],
                        "status": RunStatus.KILLED.value,
                        "parentRunId": state.get("parent_run_id"),
                    },
                )
                return {
                    "status": RunStatus.KILLED.value,
                    "error": (f"drift exceeded threshold at {threshold_label}: {report.reason}"),
                    "action_log": action_log,
                    "budget_consumed": consumed.to_wire(),
                    "critic_thresholds_hit": already_hit,
                    "drift_score": combined_score,
                }

        return {
            "action_log": action_log,
            "budget_consumed": consumed.to_wire(),
            "critic_thresholds_hit": already_hit,
            "drift_score": latest_drift,
        }

    return node


def _respond_node(
    provider: LlmProvider,
    notify: Notifier,
) -> Callable[[GraphState], Awaitable[dict[str, Any]]]:
    """Stream the final response from the provider, forwarding chunks
    as `chat.chunk` notifications and folding the final text into the
    state for downstream consumers."""

    async def node(state: GraphState) -> dict[str, Any]:
        if _is_halted(state):
            return {}
        consumed, halt = await _enter_node(state, node_name="respond", notify=notify)
        if halt is not None:
            return halt

        text_buffer: list[str] = []
        error_message: str | None = None

        raw_system_prompt = state.get("system_prompt")
        system_prompt = raw_system_prompt if isinstance(raw_system_prompt, str) else None
        chunks: AsyncIterator[ChatChunk] = provider.stream_chat(
            state["user_message"],
            system_prompt=system_prompt,
        )
        async for chunk in chunks:
            await notify(
                CHAT_CHUNK,
                {"sessionId": state["session_id"], "chunk": chunk.to_wire()},
            )
            if isinstance(chunk, ChatTextChunk):
                text_buffer.append(chunk.delta)
            elif isinstance(chunk, ChatToolCallChunk):
                entry = ActionLogEntry(
                    at_ms=_now_ms(),
                    kind="tool_call",
                    payload={
                        "callId": chunk.call_id,
                        "tool": chunk.tool,
                        "input": chunk.input,
                    },
                )
                await _emit_action(state, entry, notify)
                state = {**state, "action_log": _append_log(state, entry)}
            elif isinstance(chunk, ChatToolResultChunk):
                entry = ActionLogEntry(
                    at_ms=_now_ms(),
                    kind="tool_call",
                    payload={
                        "callId": chunk.call_id,
                        "result": chunk.output,
                        "isError": chunk.is_error,
                    },
                )
                await _emit_action(state, entry, notify)
                state = {**state, "action_log": _append_log(state, entry)}
            elif isinstance(chunk, ChatErrorChunk):
                error_message = chunk.message
            elif isinstance(chunk, ChatStartChunk | ChatStopChunk):
                pass

        final_text = "".join(text_buffer)
        # Charge the streamed response against the token budget so a
        # follow-up turn picks up where this one left off.
        consumed = consumed.with_tokens(estimate_tokens_from_text(final_text))

        post_halt = await _maybe_halt_after_work(
            state, consumed, node_name="respond", notify=notify
        )
        if post_halt is not None:
            return post_halt

        if error_message is not None:
            await _emit_status(state, RunStatus.ERRORED, notify)
            return {
                "final_response": final_text,
                "error": error_message,
                "status": RunStatus.ERRORED.value,
                "budget_consumed": consumed.to_wire(),
            }

        await _emit_status(state, RunStatus.COMPLETED, notify)
        return {
            "final_response": final_text,
            "status": RunStatus.COMPLETED.value,
            "budget_consumed": consumed.to_wire(),
        }

    return node


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _emit_status(
    state: GraphState,
    status: RunStatus,
    notify: Notifier,
) -> None:
    await notify(
        RUN_STATUS,
        {
            "runId": state["run_id"],
            "status": status.value,
            "parentRunId": state.get("parent_run_id"),
        },
    )


async def _emit_action(
    state: GraphState,
    entry: ActionLogEntry,
    notify: Notifier,
) -> None:
    await notify(
        RUN_ACTION_LOG,
        {"runId": state["run_id"], "entry": entry.to_wire()},
    )


def _append_log(state: GraphState, entry: ActionLogEntry) -> list[dict[str, Any]]:
    current: list[dict[str, Any]] = list(state.get("action_log") or [])
    current.append(entry.to_wire())
    return current
