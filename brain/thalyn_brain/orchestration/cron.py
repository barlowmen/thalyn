"""Natural-language → cron translator.

The schedule UI accepts free-form input ("every weekday at 6 a.m.")
and asks the brain to convert it to a standard 5-field cron
expression. The cron string is the source of truth — every schedule
stored in the runs index keeps both the original NL phrase (for
display) and the cron string (for OS registration).

The LLM provides the translation; ``croniter`` validates the result
so an off-shape response from the model can't poison the schedule
table. Invalid output falls back with a clear error so the renderer
can prompt the user to clarify.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from croniter import croniter  # type: ignore[import-untyped]

from thalyn_brain.provider import ChatTextChunk, LlmProvider

CRON_SYSTEM_PROMPT = """You translate natural-language schedules to standard
5-field cron expressions (minute hour day-of-month month day-of-week).

Respond with a single JSON object matching this exact shape and
nothing else:

{
  "cron": "<5-field cron expression, e.g. \"0 6 * * 1-5\">",
  "explanation": "<one sentence describing when the schedule fires>"
}

Use ``*`` for any unconstrained field. Day-of-week uses ``0`` for
Sunday and ``1-5`` for weekdays. Use 24-hour times. Do not include
seconds, descriptors (``@daily``), or non-standard syntax. Do not
include any prose outside the JSON object.
"""


@dataclass(frozen=True)
class CronTranslation:
    """Outcome of one NL→cron translation."""

    cron: str
    explanation: str
    nl_input: str
    valid: bool
    error: str | None = None

    def to_wire(self) -> dict[str, object]:
        return {
            "cron": self.cron,
            "explanation": self.explanation,
            "nlInput": self.nl_input,
            "valid": self.valid,
            "error": self.error,
        }


async def translate_nl_to_cron(provider: LlmProvider, nl_input: str) -> CronTranslation:
    """Drive an LLM round-trip and validate the response.

    Returns ``valid=False`` (with the error reason on the result)
    when the model's response can't be parsed or doesn't validate
    as cron — the caller decides whether to surface the error or
    retry.
    """
    stripped = nl_input.strip()
    if not stripped:
        return CronTranslation(
            cron="",
            explanation="",
            nl_input=stripped,
            valid=False,
            error="empty input",
        )

    raw = await _collect_text(provider, stripped)
    return parse_cron_response(raw, nl_input=stripped)


def parse_cron_response(raw: str, *, nl_input: str) -> CronTranslation:
    """Extract a cron expression from ``raw`` and validate it.

    Pulled out of ``translate_nl_to_cron`` so callers can re-use the
    same parse path against canned input (e.g. the cron-expert toggle
    in the UI submits a cron string directly; the same validator
    runs on it).
    """
    match = _JSON_OBJECT_RE.search(raw)
    if match is None:
        return CronTranslation(
            cron="",
            explanation="",
            nl_input=nl_input,
            valid=False,
            error="response was not JSON",
        )
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        return CronTranslation(
            cron="",
            explanation="",
            nl_input=nl_input,
            valid=False,
            error=f"JSON parse failed: {exc.msg}",
        )
    if not isinstance(payload, dict):
        return CronTranslation(
            cron="",
            explanation="",
            nl_input=nl_input,
            valid=False,
            error="response JSON was not an object",
        )

    cron_value = payload.get("cron")
    explanation_value = payload.get("explanation", "")
    if not isinstance(cron_value, str) or not cron_value.strip():
        return CronTranslation(
            cron="",
            explanation="",
            nl_input=nl_input,
            valid=False,
            error="missing or empty `cron` field",
        )
    explanation = explanation_value if isinstance(explanation_value, str) else ""

    return validate_cron(cron_value.strip(), explanation=explanation, nl_input=nl_input)


def validate_cron(
    cron_string: str,
    *,
    explanation: str = "",
    nl_input: str = "",
) -> CronTranslation:
    """Run a cron string through ``croniter`` and return a typed result."""
    if not croniter.is_valid(cron_string):
        return CronTranslation(
            cron=cron_string,
            explanation=explanation,
            nl_input=nl_input,
            valid=False,
            error="cron expression failed validation",
        )
    fields = cron_string.split()
    if len(fields) not in {5, 6}:
        return CronTranslation(
            cron=cron_string,
            explanation=explanation,
            nl_input=nl_input,
            valid=False,
            error="cron expression must have 5 or 6 fields",
        )
    return CronTranslation(
        cron=cron_string,
        explanation=explanation,
        nl_input=nl_input,
        valid=True,
    )


_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


async def _collect_text(provider: LlmProvider, prompt: str) -> str:
    parts: list[str] = []
    async for chunk in provider.stream_chat(prompt, system_prompt=CRON_SYSTEM_PROMPT):
        if isinstance(chunk, ChatTextChunk):
            parts.append(chunk.delta)
    return "".join(parts)
