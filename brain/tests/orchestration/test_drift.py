"""Heuristic drift-score tests."""

from __future__ import annotations

from typing import Any

from thalyn_brain.orchestration.drift import combined_drift, compute_drift_score


def _node(node_id: str, description: str) -> dict[str, Any]:
    return {"id": node_id, "description": description, "rationale": ""}


def test_drift_is_zero_for_empty_plan() -> None:
    assert compute_drift_score(None, []) == 0.0
    assert compute_drift_score({}, []) == 0.0
    assert compute_drift_score({"goal": "x", "nodes": []}, []) == 0.0


def test_drift_is_zero_when_action_log_is_empty_for_a_fresh_run() -> None:
    plan = {"goal": "x", "nodes": [_node("step_1", "Audit the call sites")]}
    assert compute_drift_score(plan, []) == 0.0


def test_drift_is_zero_when_action_log_references_plan_node_id() -> None:
    plan = {"goal": "x", "nodes": [_node("step_1", "Audit the call sites")]}
    log = [
        {
            "kind": "decision",
            "payload": {"step": "spawn_subagent", "planNodeId": "step_1"},
        }
    ]
    assert compute_drift_score(plan, log) == 0.0


def test_drift_is_zero_when_action_log_mentions_keywords_from_description() -> None:
    plan = {
        "goal": "x",
        "nodes": [_node("step_1", "Refactor the logging middleware")],
    }
    log = [
        {
            "kind": "tool_call",
            "payload": {"tool": "Bash", "input": {"command": "grep -R 'logging' src"}},
        }
    ]
    assert compute_drift_score(plan, log) == 0.0


def test_drift_is_one_when_no_node_has_evidence() -> None:
    plan = {
        "goal": "x",
        "nodes": [
            _node("step_1", "Refactor the logging middleware"),
            _node("step_2", "Update the auth tests"),
        ],
    }
    log = [
        {
            "kind": "tool_call",
            "payload": {"tool": "Bash", "input": {"command": "ls -la"}},
        }
    ]
    assert compute_drift_score(plan, log) == 1.0


def test_drift_is_partial_when_some_nodes_match() -> None:
    plan = {
        "goal": "x",
        "nodes": [
            _node("step_1", "Refactor the logging middleware"),
            _node("step_2", "Update the auth tests"),
        ],
    }
    # Only step_1 has supporting evidence.
    log = [
        {
            "kind": "decision",
            "payload": {"step": "spawn_subagent", "planNodeId": "step_1"},
        }
    ]
    assert compute_drift_score(plan, log) == 0.5


def test_combined_drift_returns_max_of_signals() -> None:
    plan = {
        "goal": "x",
        "nodes": [_node("step_1", "Refactor logging")],
    }
    # Empty log → heuristic is 0 (treats as fresh, not yet drifting).
    assert combined_drift(0.5, plan, []) == 0.5

    # A single off-plan tool call → heuristic ramps to 1.0; LLM was
    # confidently low. The reported score is the max.
    off_plan_log: list[dict[str, Any]] = [
        {"kind": "tool_call", "payload": {"tool": "Bash", "input": {"command": "ls"}}}
    ]
    assert combined_drift(0.1, plan, off_plan_log) == 1.0


def test_drift_ignores_stopwords_in_description() -> None:
    """Generic words like 'the' shouldn't match every payload."""
    plan = {
        "goal": "x",
        "nodes": [_node("step_1", "the to step")],
    }
    log = [{"kind": "tool_call", "payload": {"tool": "Bash", "input": {"command": "ls"}}}]
    # Description has only stopwords → no keywords → no match.
    assert compute_drift_score(plan, log) == 1.0
