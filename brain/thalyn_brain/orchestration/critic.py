"""Critic-agent helpers — drift-aware checkpoint review.

The critic runs at budget percentages (25 / 50 / 75 %) and after the
respond step. It looks at the plan + action log and returns a
``CriticReport`` carrying a 0-1 drift score plus a human-readable
reason. The runner consults the report at the gate: a high score
fires `run.approval_required` with `gateKind: "drift"` and halts the
run pending review.

The actual scoring lives in `drift.py` (post-v0.8 commit). This
module owns the LLM round-trip and the JSON parse so the rest of
the graph can treat the critic as a black box.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from thalyn_brain.provider import ChatTextChunk, LlmProvider

CRITIC_SYSTEM_PROMPT = """You are the critic agent inside a brain runtime.

Given the user's goal, the current plan, and the action log so far,
decide whether the agent is still on track. Respond with a single
JSON object matching this exact shape and nothing else:

{
  "drift_score": <number between 0.0 and 1.0>,
  "on_track": <boolean>,
  "reason": "<one or two sentences explaining the score>"
}

A drift_score near 0 means the actions match the plan; near 1 means
the agent has wandered off-goal. Use 0.7 as the rough cutoff between
"continue" and "ask the user to intervene". Do not include any prose
outside the JSON object.
"""


THRESHOLDS: tuple[float, ...] = (0.25, 0.5, 0.75)
"""Default budget-percentage cutoffs at which the critic fires."""


DEFAULT_DRIFT_PAUSE_THRESHOLD: float = 0.7
"""Drift score at and above which the runner halts pending review."""


@dataclass(frozen=True)
class CriticReport:
    threshold: str
    drift_score: float
    on_track: bool
    reason: str
    raw_text: str

    def to_wire(self) -> dict[str, Any]:
        return {
            "threshold": self.threshold,
            "driftScore": self.drift_score,
            "onTrack": self.on_track,
            "reason": self.reason,
        }


async def run_critic_checkpoint(
    provider: LlmProvider,
    *,
    user_message: str,
    plan: dict[str, Any] | None,
    action_log: list[dict[str, Any]],
    threshold_label: str,
) -> CriticReport:
    """Ask ``provider`` for a critic assessment and parse the result.

    Falls back to a low-confidence "on track" report when the LLM
    response can't be parsed — the run keeps going rather than
    halting on a flaky parse.
    """
    prompt = _build_critic_prompt(
        user_message=user_message,
        plan=plan,
        action_log=action_log,
        threshold_label=threshold_label,
    )
    text = await _collect_text(provider, prompt)
    score, on_track, reason = _parse_critic_response(text)
    return CriticReport(
        threshold=threshold_label,
        drift_score=score,
        on_track=on_track,
        reason=reason,
        raw_text=text,
    )


def crossed_thresholds(
    consumed: dict[str, Any],
    budget: dict[str, Any] | None,
    *,
    already_hit: list[str],
    cutoffs: tuple[float, ...] = THRESHOLDS,
) -> list[str]:
    """Return the threshold labels (e.g. ``"50%"``) newly crossed by
    the latest consumption snapshot, given which were already hit.
    Considers the cap dimensions present in ``budget`` only — an
    unbounded budget yields no crossings.
    """
    if not budget:
        return []
    fractions: list[float] = []
    max_tokens = budget.get("maxTokens")
    if isinstance(max_tokens, int) and max_tokens > 0:
        fractions.append(_safe_div(consumed.get("tokensUsed", 0), max_tokens))
    max_seconds = budget.get("maxSeconds")
    if isinstance(max_seconds, int | float) and max_seconds > 0:
        fractions.append(_safe_div(consumed.get("elapsedSeconds", 0.0), max_seconds))
    max_iterations = budget.get("maxIterations")
    if isinstance(max_iterations, int) and max_iterations > 0:
        fractions.append(_safe_div(consumed.get("iterations", 0), max_iterations))
    if not fractions:
        return []
    fraction_now = max(fractions)
    out: list[str] = []
    for cutoff in cutoffs:
        label = _label_for(cutoff)
        if label in already_hit:
            continue
        if fraction_now >= cutoff:
            out.append(label)
    return out


def _label_for(cutoff: float) -> str:
    return f"{round(cutoff * 100)}%"


def _safe_div(numerator: float, denominator: float) -> float:
    if denominator <= 0:
        return 0.0
    return float(numerator) / float(denominator)


def _build_critic_prompt(
    *,
    user_message: str,
    plan: dict[str, Any] | None,
    action_log: list[dict[str, Any]],
    threshold_label: str,
) -> str:
    plan_summary = _format_plan(plan)
    log_summary = _format_log(action_log)
    return (
        f"User goal: {user_message}\n\n"
        f"Plan:\n{plan_summary}\n\n"
        f"Action log:\n{log_summary}\n\n"
        f"Budget checkpoint: {threshold_label} consumed.\n\n"
        "Respond with the JSON object specified by the system prompt."
    )


def _format_plan(plan: dict[str, Any] | None) -> str:
    if not plan:
        return "(no plan recorded)"
    goal = plan.get("goal", "")
    nodes = plan.get("nodes") or []
    lines = [f"Goal: {goal}"]
    for index, node in enumerate(nodes, start=1):
        if not isinstance(node, dict):
            continue
        description = node.get("description", "")
        rationale = node.get("rationale", "")
        lines.append(f"  {index}. {description} — {rationale}")
    return "\n".join(lines)


def _format_log(action_log: list[dict[str, Any]]) -> str:
    if not action_log:
        return "(no actions recorded)"
    out: list[str] = []
    for entry in action_log[-30:]:
        kind = entry.get("kind", "?")
        payload = entry.get("payload") or {}
        if isinstance(payload, dict):
            step = payload.get("step") or payload.get("tool") or payload.get("from")
            out.append(f"- {kind}: {step}")
        else:
            out.append(f"- {kind}")
    return "\n".join(out) if out else "(no parseable entries)"


async def _collect_text(provider: LlmProvider, prompt: str) -> str:
    parts: list[str] = []
    async for chunk in provider.stream_chat(prompt, system_prompt=CRITIC_SYSTEM_PROMPT):
        if isinstance(chunk, ChatTextChunk):
            parts.append(chunk.delta)
    return "".join(parts)


_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_critic_response(text: str) -> tuple[float, bool, str]:
    """Permissive JSON extraction with a safe fallback on any parse miss."""
    match = _JSON_OBJECT_RE.search(text)
    if match is None:
        return 0.0, True, "critic response was not JSON; treating as on-track"
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        return 0.0, True, "critic JSON failed to parse; treating as on-track"
    if not isinstance(payload, dict):
        return 0.0, True, "critic JSON was not an object"
    score_raw = payload.get("drift_score")
    on_track_raw = payload.get("on_track")
    reason_raw = payload.get("reason", "")
    score: float
    if isinstance(score_raw, int | float):
        score = max(0.0, min(1.0, float(score_raw)))
    else:
        score = 0.0
    on_track = bool(on_track_raw) if on_track_raw is not None else score < 0.7
    reason = reason_raw if isinstance(reason_raw, str) else ""
    return score, on_track, reason
