"""LangGraph state types for the brain orchestrator.

Mirrors the data model in `02-architecture.md` §5: every run has a
plan (tree of nodes), an action log (append-only stream), and a
status that progresses through the graph nodes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal, TypedDict


class RunStatus(StrEnum):
    """Status transitions a run progresses through."""

    PENDING = "pending"
    PLANNING = "planning"
    AWAITING_APPROVAL = "awaiting_approval"  # v0.5
    RUNNING = "running"
    PAUSED = "paused"  # v0.5+
    COMPLETED = "completed"
    ERRORED = "errored"
    KILLED = "killed"


class PlanNodeStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    ERRORED = "errored"
    SKIPPED = "skipped"


@dataclass
class PlanNode:
    """One step inside a plan tree.

    ``subagent_kind`` is the planner's signal that this step should be
    handed off to a focused worker (research, edit, tool, …) instead of
    inlined into the respond turn. ``None`` keeps the historical
    pass-through behaviour.
    """

    id: str
    order: int
    description: str
    rationale: str = ""
    estimated_cost: dict[str, Any] = field(default_factory=dict)
    status: PlanNodeStatus = PlanNodeStatus.PENDING
    parent_id: str | None = None
    subagent_kind: str | None = None
    sandbox_tier: str | None = None

    def to_wire(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "order": self.order,
            "description": self.description,
            "rationale": self.rationale,
            "estimatedCost": self.estimated_cost,
            "status": self.status.value,
            "parentId": self.parent_id,
            "subagentKind": self.subagent_kind,
            "sandboxTier": self.sandbox_tier,
        }


@dataclass
class Plan:
    """The plan a run is executing against."""

    goal: str
    nodes: list[PlanNode] = field(default_factory=list)

    def to_wire(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "nodes": [n.to_wire() for n in self.nodes],
        }


ActionKind = Literal[
    "tool_call",
    "llm_call",
    "decision",
    "file_change",
    "approval",
    "drift_check",
    "node_transition",
]


@dataclass
class ActionLogEntry:
    """One append-only entry on the action log."""

    at_ms: int
    kind: ActionKind
    payload: dict[str, Any]

    def to_wire(self) -> dict[str, Any]:
        return {"atMs": self.at_ms, "kind": self.kind, "payload": self.payload}


@dataclass
class SubAgentResult:
    """Outcome of a spawned sub-agent run, as returned by the spawner."""

    parent_run_id: str
    child_run_id: str
    plan_node_id: str
    status: str
    final_response: str

    def to_wire(self) -> dict[str, Any]:
        return {
            "parentRunId": self.parent_run_id,
            "childRunId": self.child_run_id,
            "planNodeId": self.plan_node_id,
            "status": self.status,
            "finalResponse": self.final_response,
        }


class GraphState(TypedDict, total=False):
    """LangGraph state schema.

    TypedDict (rather than dataclass) because LangGraph nodes return
    partial dicts that get merged into the cumulative state.
    """

    run_id: str
    session_id: str
    provider_id: str
    parent_run_id: str | None
    depth: int
    user_message: str
    plan: dict[str, Any] | None
    action_log: list[dict[str, Any]]
    status: str
    final_response: str
    error: str | None
    subagent_results: list[dict[str, Any]]
    budget: dict[str, Any] | None
    budget_consumed: dict[str, Any]
    critic_thresholds_hit: list[str]
    drift_score: float
