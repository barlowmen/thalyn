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
CHAT_CHUNK = "chat.chunk"


def _now_ms() -> int:
    return int(time.time() * 1000)


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
    graph.add_node("critic", _critic_node(notify))  # type: ignore[call-overload]
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
        await _emit_status(state, RunStatus.PLANNING, notify)

        result = await plan_for(provider, state["user_message"])
        plan_wire = result.plan.to_wire()
        await notify(
            RUN_PLAN_UPDATE,
            {"runId": state["run_id"], "plan": plan_wire},
        )

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
        return {"plan": plan_wire, "action_log": _append_log(state, entry)}

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
        await _emit_status(state, RunStatus.RUNNING, notify)
        entry = ActionLogEntry(
            at_ms=_now_ms(),
            kind="node_transition",
            payload={"from": "plan", "to": "execute"},
        )
        await _emit_action(state, entry, notify)
        action_log = _append_log(state, entry)
        state = {**state, "action_log": action_log}

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
        }

    return node


def _critic_node(
    notify: Notifier,
) -> Callable[[GraphState], Awaitable[dict[str, Any]]]:
    """Pass-through gate; the real critic + drift monitor land in v0.8."""

    async def node(state: GraphState) -> dict[str, Any]:
        entry = ActionLogEntry(
            at_ms=_now_ms(),
            kind="node_transition",
            payload={"from": "execute", "to": "critic"},
        )
        await _emit_action(state, entry, notify)
        return {"action_log": _append_log(state, entry)}

    return node


def _respond_node(
    provider: LlmProvider,
    notify: Notifier,
) -> Callable[[GraphState], Awaitable[dict[str, Any]]]:
    """Stream the final response from the provider, forwarding chunks
    as `chat.chunk` notifications and folding the final text into the
    state for downstream consumers."""

    async def node(state: GraphState) -> dict[str, Any]:
        text_buffer: list[str] = []
        error_message: str | None = None

        chunks: AsyncIterator[ChatChunk] = provider.stream_chat(state["user_message"])
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

        if error_message is not None:
            await _emit_status(state, RunStatus.ERRORED, notify)
            return {
                "final_response": final_text,
                "error": error_message,
                "status": RunStatus.ERRORED.value,
            }

        await _emit_status(state, RunStatus.COMPLETED, notify)
        return {
            "final_response": final_text,
            "status": RunStatus.COMPLETED.value,
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
        {"runId": state["run_id"], "status": status.value},
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
