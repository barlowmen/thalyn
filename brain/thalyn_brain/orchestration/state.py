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
    """One step inside a plan tree. v0.4 uses a flat list (no nesting);
    parent_id is wired in for v0.5+ when sub-tasks land."""

    id: str
    order: int
    description: str
    rationale: str = ""
    estimated_cost: dict[str, Any] = field(default_factory=dict)
    status: PlanNodeStatus = PlanNodeStatus.PENDING
    parent_id: str | None = None

    def to_wire(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "order": self.order,
            "description": self.description,
            "rationale": self.rationale,
            "estimatedCost": self.estimated_cost,
            "status": self.status.value,
            "parentId": self.parent_id,
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


class GraphState(TypedDict, total=False):
    """LangGraph state schema.

    TypedDict (rather than dataclass) because LangGraph nodes return
    partial dicts that get merged into the cumulative state.
    """

    run_id: str
    session_id: str
    provider_id: str
    user_message: str
    plan: dict[str, Any] | None
    action_log: list[dict[str, Any]]
    status: str
    final_response: str
    error: str | None
