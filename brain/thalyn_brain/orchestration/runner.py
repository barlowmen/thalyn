"""Run a user prompt through the LangGraph orchestrator.

A `Runner` ties three things together: the provider registry that
serves LLM traffic, a notifier that the graph nodes use to push
plan/action/status events to the renderer, and the resulting
GraphState the dispatcher hands back to the caller. v0.4 ships an
in-memory checkpointer; the per-run SqliteSaver lands in commit 2.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from thalyn_brain.orchestration.graph import (
    RUN_STATUS,
    Notifier,
    build_graph,
)
from thalyn_brain.orchestration.state import GraphState, RunStatus
from thalyn_brain.provider import ProviderRegistry


@dataclass
class RunResult:
    run_id: str
    session_id: str
    provider_id: str
    status: str
    final_response: str
    plan: dict[str, Any] | None
    action_log_size: int


CheckpointerFactory = Callable[[str], Awaitable[Any | None]]


class Runner:
    """Drives the brain graph for a single user-prompt turn."""

    def __init__(
        self,
        registry: ProviderRegistry,
        *,
        checkpointer_factory: CheckpointerFactory | None = None,
    ) -> None:
        self._registry = registry
        self._checkpointer_factory = checkpointer_factory

    async def run(
        self,
        *,
        session_id: str,
        provider_id: str,
        prompt: str,
        notify: Notifier,
        run_id: str | None = None,
    ) -> RunResult:
        provider = self._registry.get(provider_id)

        run_id = run_id or _new_run_id()

        await notify(
            RUN_STATUS,
            {"runId": run_id, "status": RunStatus.PENDING.value},
        )

        checkpointer: Any | None = None
        if self._checkpointer_factory is not None:
            checkpointer = await self._checkpointer_factory(run_id)

        graph = build_graph(provider, notify, checkpointer=checkpointer)

        initial: GraphState = {
            "run_id": run_id,
            "session_id": session_id,
            "provider_id": provider_id,
            "user_message": prompt,
            "plan": None,
            "action_log": [],
            "status": RunStatus.PENDING.value,
            "final_response": "",
            "error": None,
        }
        config = {"configurable": {"thread_id": run_id}}

        final_state: GraphState = await graph.ainvoke(initial, config=config)

        return RunResult(
            run_id=run_id,
            session_id=session_id,
            provider_id=provider_id,
            status=final_state.get("status") or RunStatus.COMPLETED.value,
            final_response=final_state.get("final_response", ""),
            plan=final_state.get("plan"),
            action_log_size=len(final_state.get("action_log") or []),
        )


def _new_run_id() -> str:
    return f"r_{int(time.time())}_{uuid.uuid4().hex[:8]}"
