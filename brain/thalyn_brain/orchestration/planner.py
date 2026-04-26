"""LLM-driven plan generation.

Asks the active provider to emit a structured plan as JSON, then
parses it into the orchestrator's `Plan` shape. Falls back to a
single-step plan when the model declines to break the task down or
when the response can't be parsed — the orchestrator always has
*something* to render in the inspector before execute runs.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass

from thalyn_brain.orchestration.state import Plan, PlanNode, PlanNodeStatus
from thalyn_brain.provider import ChatTextChunk, LlmProvider

PLAN_SYSTEM_PROMPT = """You are the planning step of an agent runtime.

Given the user's request, decide if the task warrants a multi-step
plan. Respond with a single JSON object matching this exact shape and
nothing else:

{
  "goal": "<one-sentence summary of the user's goal>",
  "steps": [
    {
      "description": "<imperative, action-focused step>",
      "rationale": "<why this step is needed>",
      "estimated_tokens": <integer>
    }
  ]
}

If the task is a simple question or doesn't need decomposition, emit
a single step that is "Answer the user's question." with rationale
"Trivial response; no decomposition needed." and estimated_tokens
between 100 and 500. Otherwise produce 2 to 6 steps.

Do not include any prose outside the JSON object.
"""


@dataclass
class PlannerResult:
    plan: Plan
    raw_text: str


async def plan_for(provider: LlmProvider, user_message: str) -> PlannerResult:
    """Drive the planner provider call and parse the response.

    The planner uses the same provider as the eventual executor so the
    plan reflects what the executor can do; a future refinement may
    route planning to a smaller / cheaper model.
    """
    text = await _collect_planner_text(provider, user_message)
    parsed = _parse_plan(text, fallback_goal=user_message)
    return PlannerResult(plan=parsed, raw_text=text)


async def _collect_planner_text(provider: LlmProvider, user_message: str) -> str:
    """Drain a non-streaming planning turn and return the assembled text.

    Tool calls and tool results are intentionally ignored — the
    planner is a "describe a plan" turn, not an "execute" turn.
    """
    parts: list[str] = []
    async for chunk in provider.stream_chat(
        user_message, system_prompt=PLAN_SYSTEM_PROMPT
    ):
        if isinstance(chunk, ChatTextChunk):
            parts.append(chunk.delta)
    return "".join(parts)


def _parse_plan(text: str, *, fallback_goal: str) -> Plan:
    payload = _extract_json(text)
    if payload is None:
        return _fallback_plan(fallback_goal, reason="planner output was not JSON")

    if not isinstance(payload, dict):
        return _fallback_plan(fallback_goal, reason="planner JSON was not an object")

    goal = payload.get("goal")
    steps = payload.get("steps")
    if not isinstance(goal, str) or not goal.strip():
        goal = fallback_goal
    if not isinstance(steps, list) or not steps:
        return _fallback_plan(fallback_goal, reason="planner JSON had no steps")

    nodes: list[PlanNode] = []
    for index, raw in enumerate(steps):
        if not isinstance(raw, dict):
            continue
        description = raw.get("description")
        if not isinstance(description, str) or not description.strip():
            continue
        rationale_value = raw.get("rationale", "")
        rationale = rationale_value if isinstance(rationale_value, str) else ""
        estimated = raw.get("estimated_tokens")
        cost: dict[str, int | float] = {}
        if isinstance(estimated, int | float):
            cost["tokens"] = int(estimated)
        nodes.append(
            PlanNode(
                id=f"step_{uuid.uuid4().hex[:8]}",
                order=index,
                description=description.strip(),
                rationale=rationale.strip(),
                estimated_cost=cost,
                status=PlanNodeStatus.PENDING,
            )
        )

    if not nodes:
        return _fallback_plan(fallback_goal, reason="no usable steps in JSON")

    return Plan(goal=goal, nodes=nodes)


_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json(text: str) -> object | None:
    """Find the first balanced JSON object in the planner's text.

    Permissive about prose around the JSON because models sometimes
    add a sentence even when asked not to.
    """
    match = _JSON_OBJECT_RE.search(text)
    if not match:
        return None
    try:
        parsed: object = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return parsed


def _fallback_plan(goal: str, *, reason: str) -> Plan:
    return Plan(
        goal=goal,
        nodes=[
            PlanNode(
                id=f"step_{uuid.uuid4().hex[:8]}",
                order=0,
                description="Answer the user's question.",
                rationale=f"Fallback plan ({reason}).",
                estimated_cost={},
                status=PlanNodeStatus.PENDING,
            )
        ],
    )
