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

from thalyn_brain.audit import AuditLogWriter, wrap_notifier
from thalyn_brain.orchestration.graph import (
    RUN_PLAN_UPDATE,
    RUN_STATUS,
    Notifier,
    build_graph,
)
from thalyn_brain.orchestration.state import GraphState, RunStatus
from thalyn_brain.orchestration.storage import open_run_checkpointer
from thalyn_brain.provider import ProviderRegistry
from thalyn_brain.runs import RunHeader, RunsStore, RunUpdate

RUN_APPROVAL_REQUIRED = "run.approval_required"


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
        # Audit logs land alongside the per-run db, so we keep a copy
        # of data_dir even when the caller passed in a custom
        # checkpointer context.
        self._audit_data_dir = data_dir

    def _audit_for(self, run_id: str) -> AuditLogWriter | None:
        if self._audit_data_dir is None:
            return None
        return AuditLogWriter(run_id, data_dir=self._audit_data_dir)

    async def resume(
        self,
        *,
        run_id: str,
        provider_id: str,
        notify: Notifier,
    ) -> RunResult | None:
        """Resume an in-flight run from its last checkpoint.

        Returns ``None`` when the per-run db has no checkpoint to
        resume from (typical when a run never made it past status
        insertion). On success the runs index is updated to reflect
        the final state.
        """
        provider = self._registry.get(provider_id)
        audit = self._audit_for(run_id)
        notify = wrap_notifier(notify, audit)

        async with self._checkpointer_context(run_id) as checkpointer:
            if checkpointer is None:
                # No persistence — nothing to resume against.
                return None
            graph = build_graph(provider, notify, checkpointer=checkpointer)
            config = {"configurable": {"thread_id": run_id}}

            existing = await graph.aget_state(config)
            if existing is None or not existing.values:
                return None

            final_state = await graph.ainvoke(None, config=config)

        return await self._finalize(
            run_id=run_id,
            provider_id=provider_id,
            session_id=final_state.get("session_id", ""),
            final_state=final_state,
        )

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

        audit = self._audit_for(run_id)
        notify = wrap_notifier(notify, audit)

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

            final_state = await graph.ainvoke(initial, config=config)

            # If the graph paused at the plan-approval interrupt, surface
            # that status to the caller and notify the renderer so the
            # approval modal can open. The run continues async via a
            # subsequent approve_plan call.
            if checkpointer is not None and await _paused_at_interrupt(graph, config):
                return await self._handle_interrupt(
                    run_id=run_id,
                    session_id=session_id,
                    provider_id=provider_id,
                    final_state=final_state,
                    notify=notify,
                )

        return await self._finalize(
            run_id=run_id,
            provider_id=provider_id,
            session_id=session_id,
            final_state=final_state,
        )

    async def approve_plan(
        self,
        *,
        run_id: str,
        provider_id: str,
        decision: str,
        notify: Notifier,
        edited_plan: dict[str, Any] | None = None,
    ) -> RunResult | None:
        """Resolve an in-flight approval gate.

        ``decision`` is "approve", "edit", or "reject". On "edit" the
        provided ``edited_plan`` replaces the planner's output before
        execution resumes. On "reject" the run is marked killed and
        the graph is not resumed. ``approve`` and ``edit`` resume the
        graph from the interrupt; the final state is returned to the
        caller and the runs index is updated.
        """
        if decision not in {"approve", "edit", "reject"}:
            raise ValueError(f"unknown decision: {decision}")
        provider = self._registry.get(provider_id)
        audit = self._audit_for(run_id)
        if audit is not None:
            audit.append_approval(decision, edited_plan_present=edited_plan is not None)
        notify = wrap_notifier(notify, audit)

        async with self._checkpointer_context(run_id) as checkpointer:
            if checkpointer is None:
                return None
            graph = build_graph(provider, notify, checkpointer=checkpointer)
            config = {"configurable": {"thread_id": run_id}}

            existing = await graph.aget_state(config)
            if existing is None or not existing.values:
                return None

            if decision == "reject":
                # Mark the run killed and skip resumption.
                await notify(
                    RUN_STATUS,
                    {"runId": run_id, "status": RunStatus.KILLED.value},
                )
                final_state = {
                    **existing.values,
                    "status": RunStatus.KILLED.value,
                }
                return await self._finalize(
                    run_id=run_id,
                    provider_id=provider_id,
                    session_id=final_state.get("session_id", ""),
                    final_state=final_state,
                )

            if decision == "edit" and edited_plan is not None:
                # Overwrite the cached plan in the checkpoint state so the
                # downstream nodes execute against the user's edit.
                await graph.aupdate_state(config, {"plan": edited_plan})
                await notify(
                    RUN_PLAN_UPDATE,
                    {"runId": run_id, "plan": edited_plan},
                )

            final_state = await graph.ainvoke(None, config=config)

        return await self._finalize(
            run_id=run_id,
            provider_id=provider_id,
            session_id=final_state.get("session_id", ""),
            final_state=final_state,
        )

    async def _handle_interrupt(
        self,
        *,
        run_id: str,
        session_id: str,
        provider_id: str,
        final_state: dict[str, Any],
        notify: Notifier,
    ) -> RunResult:
        await notify(
            RUN_STATUS,
            {"runId": run_id, "status": RunStatus.AWAITING_APPROVAL.value},
        )
        await notify(
            RUN_APPROVAL_REQUIRED,
            {
                "runId": run_id,
                "gateKind": "plan",
                "plan": final_state.get("plan"),
            },
        )

        if self._runs_store is not None:
            await self._runs_store.update(
                run_id,
                RunUpdate(status=RunStatus.AWAITING_APPROVAL.value).with_plan(
                    final_state.get("plan")
                ),
            )

        return RunResult(
            run_id=run_id,
            session_id=session_id,
            provider_id=provider_id,
            status=RunStatus.AWAITING_APPROVAL.value,
            final_response="",
            plan=final_state.get("plan"),
            action_log_size=len(final_state.get("action_log") or []),
        )

    async def _finalize(
        self,
        *,
        run_id: str,
        provider_id: str,
        session_id: str,
        final_state: dict[str, Any],
    ) -> RunResult:
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


async def _paused_at_interrupt(graph: Any, config: dict[str, Any]) -> bool:
    """True when the graph is currently parked at the plan-approval gate."""
    snapshot = await graph.aget_state(config)
    if snapshot is None:
        return False
    next_nodes: tuple[str, ...] = getattr(snapshot, "next", ()) or ()
    return "execute" in next_nodes


def _new_run_id() -> str:
    return f"r_{int(time.time())}_{uuid.uuid4().hex[:8]}"


def _title_from(prompt: str) -> str:
    """First non-empty line, capped at 80 chars — used in the runs index UI."""
    for line in prompt.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:80]
    return prompt[:80]
