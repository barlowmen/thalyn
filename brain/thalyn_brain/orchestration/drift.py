"""Heuristic drift-score computation.

The critic LLM produces a drift verdict, but trust-but-verify: a
local heuristic gives the runtime its own opinion of how well the
agent's actions track the plan. The reported drift score is the
``max`` of the two — when either signal thinks the agent has
wandered, the run pauses for review.

The heuristic is intentionally simple: drift is the fraction of
plan nodes that have *no* matching evidence in the action log.
A node is matched when an action-log payload carries its `planNodeId`
verbatim or when the entry's payload contains tokens drawn from the
node's description. That covers both first-class match (e.g.
sub-agent spawn for that plan node) and looser tool-call matches
where the agent is clearly working on the step.
"""

from __future__ import annotations

import re
from typing import Any

# Words that don't carry meaning when matching plan-node descriptions
# against action-log payloads — too generic to imply on-task work.
_STOPWORDS: frozenset[str] = frozenset(
    {
        "a",
        "an",
        "and",
        "any",
        "are",
        "as",
        "at",
        "be",
        "by",
        "do",
        "for",
        "from",
        "has",
        "have",
        "in",
        "into",
        "is",
        "it",
        "of",
        "on",
        "or",
        "out",
        "step",
        "steps",
        "the",
        "then",
        "this",
        "to",
        "up",
        "use",
        "with",
    }
)


def compute_drift_score(
    plan: dict[str, Any] | None,
    action_log: list[dict[str, Any]],
) -> float:
    """Return a 0.0-1.0 drift estimate.

    ``0`` means every plan node has at least one matching action-log
    entry; ``1`` means none did. The heuristic only kicks in when the
    log carries ``work_entries`` — tool calls or non-structural
    decisions like sub-agent spawns. Pure node-transition / budget /
    critic entries are bookkeeping the runtime emits regardless of
    what the agent did, so a quiet log isn't proof of drift.
    """
    if not plan:
        return 0.0
    nodes = plan.get("nodes") or []
    if not nodes:
        return 0.0
    work_entries = _filter_work_entries(action_log)
    if not work_entries:
        return 0.0

    matched_count = 0
    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_id = node.get("id")
        description = node.get("description", "")
        keywords = _keywords_from(description)
        if _node_has_evidence(node_id, keywords, work_entries):
            matched_count += 1

    drift = 1.0 - (matched_count / len(nodes))
    return max(0.0, min(1.0, drift))


def _filter_work_entries(
    action_log: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Drop bookkeeping entries the runtime emits regardless of
    agent behaviour. The remaining entries are the ones that
    represent the agent doing work the plan should account for."""
    out: list[dict[str, Any]] = []
    for entry in action_log:
        kind = entry.get("kind")
        if kind == "tool_call":
            out.append(entry)
            continue
        if kind == "decision":
            payload = entry.get("payload") or {}
            step = payload.get("step") if isinstance(payload, dict) else None
            # `plan` and `critic` are runtime bookkeeping; the
            # agent-work decisions are everything else (sub-agent
            # spawns, file changes, …).
            if step in {"plan", "critic"}:
                continue
            out.append(entry)
    return out


def combined_drift(
    llm_score: float,
    plan: dict[str, Any] | None,
    log: list[dict[str, Any]],
) -> float:
    """``max`` of the LLM verdict and the local heuristic, clamped to
    [0, 1]. The two signals act independently — if either flags
    drift, the runtime treats the run as drifted."""
    heuristic = compute_drift_score(plan, log)
    return max(0.0, min(1.0, max(llm_score, heuristic)))


def _keywords_from(description: str) -> set[str]:
    if not description:
        return set()
    tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9_]+", description.lower())
    return {token for token in tokens if len(token) > 2 and token not in _STOPWORDS}


def _node_has_evidence(
    node_id: Any,
    keywords: set[str],
    action_log: list[dict[str, Any]],
) -> bool:
    for entry in action_log:
        payload = entry.get("payload") or {}
        if not isinstance(payload, dict):
            continue
        if node_id and payload.get("planNodeId") == node_id:
            return True
        if keywords and _payload_mentions(payload, keywords):
            return True
    return False


def _payload_mentions(payload: dict[str, Any], keywords: set[str]) -> bool:
    haystack = " ".join(_strings_in(payload)).lower()
    if not haystack:
        return False
    return any(keyword in haystack for keyword in keywords)


def _strings_in(value: Any) -> list[str]:
    out: list[str] = []
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, dict):
        for nested in value.values():
            out.extend(_strings_in(nested))
    elif isinstance(value, list):
        for nested in value:
            out.extend(_strings_in(nested))
    return out
