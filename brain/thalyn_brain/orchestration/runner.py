"""Run a user prompt through the LangGraph orchestrator.

A `Runner` ties four things together: the provider registry that
serves LLM traffic, a notifier that the graph nodes use to push
plan/action/status events to the renderer, the per-run SqliteSaver
where the graph snapshots its state on every node transition, and a
`RunsStore` that persists the header row used by the runs index UI.
Tests can pass a no-op store to skip persistence.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from thalyn_brain.orchestration.graph import (
    RUN_STATUS,
    Notifier,
    build_graph,
)
from thalyn_brain.orchestration.state import GraphState, RunStatus
from thalyn_brain.orchestration.storage import open_run_checkpointer
from thalyn_brain.provider import ProviderRegistry
from thalyn_brain.runs import RunHeader, RunsStore, RunUpdate


@dataclass
class RunResult:
    run_id: str
    session_id: str
    provider_id: str
    status: str
    final_response: str
    plan: dict[str, Any] | None
    action_log_size: int


CheckpointerContext = Callable[[str], AbstractAsyncContextManager[Any]]
"""A factory yielding a LangGraph checkpointer for a single run."""


def _persistent_context(data_dir: Path | None) -> CheckpointerContext:
    def factory(run_id: str) -> AbstractAsyncContextManager[Any]:
        return open_run_checkpointer(run_id, data_dir=data_dir)

    return factory


@asynccontextmanager
async def _no_checkpointer_cm(_run_id: str) -> AsyncIterator[Any]:
    yield None


class Runner:
    """Drives the brain graph for a single user-prompt turn."""

    def __init__(
        self,
        registry: ProviderRegistry,
        *,
        checkpointer_context: CheckpointerContext | None = None,
        runs_store: RunsStore | None = None,
        data_dir: Path | None = None,
    ) -> None:
        self._registry = registry
        if checkpointer_context is not None:
            self._checkpointer_context: CheckpointerContext = checkpointer_context
        elif data_dir is not None:
            self._checkpointer_context = _persistent_context(data_dir)
        else:
            self._checkpointer_context = _no_checkpointer_cm
        self._runs_store = runs_store

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
        started_at = int(time.time() * 1000)
        title = _title_from(prompt)

        await notify(
            RUN_STATUS,
            {"runId": run_id, "status": RunStatus.PENDING.value},
        )

        if self._runs_store is not None:
            await self._runs_store.insert(
                RunHeader(
                    run_id=run_id,
                    project_id=None,
                    parent_run_id=None,
                    status=RunStatus.PLANNING.value,
                    title=title,
                    provider_id=provider_id,
                    started_at_ms=started_at,
                    completed_at_ms=None,
                    drift_score=0.0,
                    final_response="",
                )
            )

        async with self._checkpointer_context(run_id) as checkpointer:
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

        status = final_state.get("status") or RunStatus.COMPLETED.value
        plan = final_state.get("plan")
        final_response = final_state.get("final_response", "")

        if self._runs_store is not None:
            await self._runs_store.update(
                run_id,
                RunUpdate(
                    status=status,
                    completed_at_ms=int(time.time() * 1000),
                    final_response=final_response,
                ).with_plan(plan),
            )

        return RunResult(
            run_id=run_id,
            session_id=session_id,
            provider_id=provider_id,
            status=status,
            final_response=final_response,
            plan=plan,
            action_log_size=len(final_state.get("action_log") or []),
        )


def _new_run_id() -> str:
    return f"r_{int(time.time())}_{uuid.uuid4().hex[:8]}"


def _title_from(prompt: str) -> str:
    """First non-empty line, capped at 80 chars — used in the runs index UI."""
    for line in prompt.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:80]
    return prompt[:80]
