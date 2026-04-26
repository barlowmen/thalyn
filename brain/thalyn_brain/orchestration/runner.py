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
from thalyn_brain.orchestration.budget import Budget, BudgetConsumption
from thalyn_brain.orchestration.graph import (
    RUN_PLAN_UPDATE,
    RUN_STATUS,
    Notifier,
    build_graph,
)
from thalyn_brain.orchestration.state import GraphState, RunStatus, SubAgentResult
from thalyn_brain.orchestration.storage import open_run_checkpointer
from thalyn_brain.orchestration.subagent import SubAgentSpawner
from thalyn_brain.provider import ProviderRegistry
from thalyn_brain.runs import RunHeader, RunsStore, RunUpdate

RUN_APPROVAL_REQUIRED = "run.approval_required"
DEFAULT_DEPTH_CAP = 2


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
        depth_cap: int = DEFAULT_DEPTH_CAP,
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
        # Maximum depth of the spawned-sub-agent tree. Spawns that
        # would exceed this depth surface a depth-gate
        # ``run.approval_required`` notification and are skipped until
        # an explicit-approval surface is wired up.
        self._depth_cap = depth_cap

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
            existing_values = dict(existing.values)

            # If the run is parked at the plan-approval interrupt,
            # leave it parked — the user owns the resume by calling
            # ``approve_plan``. Auto-running past the interrupt would
            # silently make the approval gate moot after a restart.
            if await _paused_at_interrupt(graph, config):
                await notify(
                    RUN_STATUS,
                    {
                        "runId": run_id,
                        "status": RunStatus.AWAITING_APPROVAL.value,
                        "parentRunId": existing_values.get("parent_run_id"),
                    },
                )
                return RunResult(
                    run_id=run_id,
                    session_id=existing_values.get("session_id", ""),
                    provider_id=provider_id,
                    status=RunStatus.AWAITING_APPROVAL.value,
                    final_response="",
                    plan=existing_values.get("plan"),
                    action_log_size=len(existing_values.get("action_log") or []),
                )

            spawner = self._spawner_for(
                session_id=existing_values.get("session_id", ""),
                provider_id=provider_id,
                parent_notify=notify,
            )
            graph = build_graph(
                provider,
                notify,
                checkpointer=checkpointer,
                spawn_subagent=spawner,
            )

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
        parent_run_id: str | None = None,
        depth: int = 0,
        budget: Budget | None = None,
        system_prompt: str | None = None,
    ) -> RunResult:
        provider = self._registry.get(provider_id)

        run_id = run_id or _new_run_id()
        started_at = int(time.time() * 1000)
        title = _title_from(prompt)
        budget_wire = budget.to_wire() if budget is not None else None
        consumed = BudgetConsumption(started_at_ms=started_at)

        audit = self._audit_for(run_id)
        notify = wrap_notifier(notify, audit)

        await notify(
            RUN_STATUS,
            {
                "runId": run_id,
                "status": RunStatus.PENDING.value,
                "parentRunId": parent_run_id,
            },
        )

        if self._runs_store is not None:
            await self._runs_store.insert(
                RunHeader(
                    run_id=run_id,
                    project_id=None,
                    parent_run_id=parent_run_id,
                    status=RunStatus.PLANNING.value,
                    title=title,
                    provider_id=provider_id,
                    started_at_ms=started_at,
                    completed_at_ms=None,
                    drift_score=0.0,
                    final_response="",
                    budget=budget_wire,
                    budget_consumed=consumed.to_wire(),
                )
            )

        spawner = self._spawner_for(
            session_id=session_id,
            provider_id=provider_id,
            parent_notify=notify,
        )

        async with self._checkpointer_context(run_id) as checkpointer:
            graph = build_graph(
                provider,
                notify,
                checkpointer=checkpointer,
                spawn_subagent=spawner,
            )

            initial: GraphState = {
                "run_id": run_id,
                "session_id": session_id,
                "provider_id": provider_id,
                "parent_run_id": parent_run_id,
                "depth": depth,
                "user_message": prompt,
                "plan": None,
                "action_log": [],
                "status": RunStatus.PENDING.value,
                "final_response": "",
                "error": None,
                "subagent_results": [],
                "budget": budget_wire,
                "budget_consumed": consumed.to_wire(),
                "critic_thresholds_hit": [],
                "drift_score": 0.0,
                "system_prompt": system_prompt,
            }
            config = {"configurable": {"thread_id": run_id}}

            final_state = await graph.ainvoke(initial, config=config)

            # If a node halted on a budget gate the state is already
            # terminal — fall through to finalisation rather than
            # surfacing the plan-approval interrupt that would
            # otherwise have fired next.
            if final_state.get("status") not in {
                RunStatus.KILLED.value,
                RunStatus.ERRORED.value,
            }:
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

            existing_values = dict(existing.values)
            spawner = self._spawner_for(
                session_id=existing_values.get("session_id", ""),
                provider_id=provider_id,
                parent_notify=notify,
            )
            graph = build_graph(
                provider,
                notify,
                checkpointer=checkpointer,
                spawn_subagent=spawner,
            )

            if decision == "reject":
                # Mark the run killed and skip resumption.
                await notify(
                    RUN_STATUS,
                    {
                        "runId": run_id,
                        "status": RunStatus.KILLED.value,
                        "parentRunId": existing_values.get("parent_run_id"),
                    },
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
            {
                "runId": run_id,
                "status": RunStatus.AWAITING_APPROVAL.value,
                "parentRunId": final_state.get("parent_run_id"),
            },
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

    async def kill_run(
        self,
        *,
        run_id: str,
        notify: Notifier,
    ) -> RunResult | None:
        """Mark a run killed and surface the status transition.

        Used when the user aborts a sub-agent (or the parent that
        spawned one) before it reaches its terminal state. The
        in-flight asyncio task driving the graph is not forcibly
        cancelled here — the kill flips the persistent state and
        notifies the renderer; in-flight work will exit on its own
        next checkpoint barrier.
        """
        audit = self._audit_for(run_id)
        teed = wrap_notifier(notify, audit)
        if self._runs_store is None:
            await teed(
                RUN_STATUS,
                {
                    "runId": run_id,
                    "status": RunStatus.KILLED.value,
                    "parentRunId": None,
                },
            )
            return None
        header = await self._runs_store.get(run_id)
        if header is None:
            return None
        await teed(
            RUN_STATUS,
            {
                "runId": run_id,
                "status": RunStatus.KILLED.value,
                "parentRunId": header.parent_run_id,
            },
        )
        await self._runs_store.update(
            run_id,
            RunUpdate(
                status=RunStatus.KILLED.value,
                completed_at_ms=int(time.time() * 1000),
            ),
        )
        return RunResult(
            run_id=run_id,
            session_id="",
            provider_id=header.provider_id,
            status=RunStatus.KILLED.value,
            final_response=header.final_response,
            plan=header.plan,
            action_log_size=0,
        )

    def _spawner_for(
        self,
        *,
        session_id: str,
        provider_id: str,
        parent_notify: Notifier,
    ) -> SubAgentSpawner:
        """Build a closure the graph layer uses to dispatch a sub-agent.

        The graph supplies the parent's current depth on each call —
        that's the only authoritative reading once the run resumes
        across an interrupt — so the closure forwards it verbatim.
        """

        async def spawner(
            *,
            parent_run_id: str,
            plan_node: dict[str, Any],
            depth: int,
        ) -> SubAgentResult:
            return await self._spawn_subagent(
                parent_run_id=parent_run_id,
                plan_node=plan_node,
                depth=depth,
                session_id=session_id,
                provider_id=provider_id,
                parent_notify=parent_notify,
            )

        return spawner

    async def _spawn_subagent(
        self,
        *,
        parent_run_id: str,
        plan_node: dict[str, Any],
        depth: int,
        session_id: str,
        provider_id: str,
        parent_notify: Notifier,
    ) -> SubAgentResult:
        """Drive one sub-agent run to completion and return its outcome.

        The child shares the parent's session and provider but gets a
        fresh ``run_id``, its own checkpoint db, its own audit log,
        and its own ``parent_run_id`` / ``depth`` markers in state. Sub-agent
        events flow through the same notifier the parent is using —
        the renderer routes them by ``runId``.

        Spawning at a depth that exceeds the runner's depth cap fires
        a depth-gate ``run.approval_required`` notification and
        returns a skipped result without running a child graph; the
        plan node is recorded as untaken in the audit log.
        """
        plan_node_id = plan_node.get("id") or ""
        child_depth = depth + 1

        if child_depth > self._depth_cap:
            await parent_notify(
                RUN_APPROVAL_REQUIRED,
                {
                    "runId": parent_run_id,
                    "gateKind": "depth",
                    "depth": child_depth,
                    "depthCap": self._depth_cap,
                    "planNode": plan_node,
                },
            )
            return SubAgentResult(
                parent_run_id=parent_run_id,
                child_run_id="",
                plan_node_id=str(plan_node_id),
                status="skipped",
                final_response="",
            )

        child_run_id = _new_run_id()
        title = plan_node.get("description") or "Sub-agent task"
        if isinstance(title, str):
            title = title[:80]
        description = plan_node.get("description") or ""

        provider = self._registry.get(provider_id)
        audit = self._audit_for(child_run_id)
        notify = wrap_notifier(parent_notify, audit)
        started_at = int(time.time() * 1000)

        await notify(
            RUN_STATUS,
            {
                "runId": child_run_id,
                "status": RunStatus.PENDING.value,
                "parentRunId": parent_run_id,
            },
        )

        sandbox_tier_raw = plan_node.get("sandboxTier")
        sandbox_tier = sandbox_tier_raw if isinstance(sandbox_tier_raw, str) else None

        if self._runs_store is not None:
            await self._runs_store.insert(
                RunHeader(
                    run_id=child_run_id,
                    project_id=None,
                    parent_run_id=parent_run_id,
                    status=RunStatus.PLANNING.value,
                    title=str(title),
                    provider_id=provider_id,
                    started_at_ms=started_at,
                    completed_at_ms=None,
                    drift_score=0.0,
                    final_response="",
                    sandbox_tier=sandbox_tier,
                )
            )

        # Children of this child go through the same spawner, so the
        # tree can grow as deep as the depth cap allows. Each spawn
        # creates a fresh closure rather than sharing state across
        # nested calls.
        child_spawner = self._spawner_for(
            session_id=session_id,
            provider_id=provider_id,
            parent_notify=parent_notify,
        )

        async with self._checkpointer_context(child_run_id) as checkpointer:
            graph = build_graph(
                provider,
                notify,
                checkpointer=checkpointer,
                interrupt_on_plan_approval=False,
                spawn_subagent=child_spawner,
            )
            initial: GraphState = {
                "run_id": child_run_id,
                "session_id": session_id,
                "provider_id": provider_id,
                "parent_run_id": parent_run_id,
                "depth": child_depth,
                "user_message": description,
                "plan": None,
                "action_log": [],
                "status": RunStatus.PENDING.value,
                "final_response": "",
                "error": None,
                "subagent_results": [],
            }
            config = {"configurable": {"thread_id": child_run_id}}
            final_state = await graph.ainvoke(initial, config=config)

        finalised = await self._finalize(
            run_id=child_run_id,
            provider_id=provider_id,
            session_id=session_id,
            final_state=final_state,
        )
        return SubAgentResult(
            parent_run_id=parent_run_id,
            child_run_id=child_run_id,
            plan_node_id=str(plan_node_id),
            status=finalised.status,
            final_response=finalised.final_response,
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
        budget_consumed = final_state.get("budget_consumed")
        drift_score = final_state.get("drift_score")

        if self._runs_store is not None:
            await self._runs_store.update(
                run_id,
                RunUpdate(
                    status=status,
                    completed_at_ms=int(time.time() * 1000),
                    final_response=final_response,
                    drift_score=float(drift_score)
                    if isinstance(drift_score, int | float)
                    else None,
                )
                .with_plan(plan)
                .with_budget_consumed(budget_consumed),
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
