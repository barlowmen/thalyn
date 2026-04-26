"""Ghost-text inline-suggestion service.

Routes a tight code-completion prompt through the active provider and
returns the next sliver of code Monaco can render as ghost text. Uses
:func:`stream_chat` so the same providers that drive the chat surface
power inline suggest without a parallel API surface — at the cost of a
chat-style round-trip per suggestion. A future commit may add a
dedicated completion-tuned path for providers that expose one.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from thalyn_brain.provider.base import (
    ChatErrorChunk,
    ChatTextChunk,
    LlmProvider,
)

# Hard upper bound on how many characters we'll buffer back from the
# provider before truncating. Ghost-text overflows that fly off the
# end of the editor distract the user; pulling more than this is
# also a token-budget wasteline.
MAX_SUGGESTION_CHARS = 240

# Stop conditions: when the provider emits one of these, we treat
# the suggestion as complete and return what we have. Most chat
# models speak in markdown; we're after raw code, so a fence start
# means the model has shifted into commentary mode.
STOP_TOKENS: tuple[str, ...] = ("```", "\n\n")


@dataclass(frozen=True)
class InlineSuggestion:
    """One suggestion ready to render as Monaco ghost text."""

    suggestion: str
    request_id: str
    requested_at_ms: int
    completed_at_ms: int
    provider_id: str
    truncated: bool = False

    def to_wire(self) -> dict[str, object]:
        return {
            "suggestion": self.suggestion,
            "requestId": self.request_id,
            "requestedAtMs": self.requested_at_ms,
            "completedAtMs": self.completed_at_ms,
            "providerId": self.provider_id,
            "truncated": self.truncated,
        }


def build_system_prompt(language: str) -> str:
    """Tight system prompt that constrains the model to raw code."""

    lang_hint = f" (language: {language})" if language else ""
    return (
        "You are a code-completion engine"
        + lang_hint
        + ". Return only the next few characters of code that should appear "
        "at the cursor. No commentary, no markdown fences, no explanations. "
        "Stop at the natural breakpoint (end of statement, line, or block)."
    )


def build_user_prompt(prefix: str, suffix: str) -> str:
    """User-message body that frames the completion task."""

    # The cursor token is a literal string the model is told to
    # replace; it's a stable anchor inside the user message.
    return (
        "Replace the <CURSOR/> marker with the most likely next token(s).\n\n"
        f"{prefix}<CURSOR/>{suffix}\n"
    )


async def suggest(
    *,
    provider: LlmProvider,
    provider_id: str,
    request_id: str,
    prefix: str,
    suffix: str = "",
    language: str = "",
) -> InlineSuggestion:
    """Drive one round-trip against ``provider`` and return what came
    back as a single :class:`InlineSuggestion`."""

    requested_at = int(time.time() * 1000)
    system = build_system_prompt(language)
    user = build_user_prompt(prefix, suffix)

    buf: list[str] = []
    truncated = False

    try:
        async for chunk in provider.stream_chat(user, system_prompt=system):
            if isinstance(chunk, ChatErrorChunk):
                # Provider surfaced an error mid-stream; bail out.
                break
            if not isinstance(chunk, ChatTextChunk):
                continue
            buf.append(chunk.delta)
            joined = "".join(buf)
            if any(stop in joined for stop in STOP_TOKENS):
                break
            if len(joined) >= MAX_SUGGESTION_CHARS:
                truncated = True
                break
    except asyncio.CancelledError:
        raise

    raw = "".join(buf)
    if truncated:
        raw = raw[:MAX_SUGGESTION_CHARS]
    suggestion = _trim_suggestion(raw)
    completed_at = int(time.time() * 1000)
    return InlineSuggestion(
        suggestion=suggestion,
        request_id=request_id,
        requested_at_ms=requested_at,
        completed_at_ms=completed_at,
        provider_id=provider_id,
        truncated=truncated,
    )


def _trim_suggestion(text: str) -> str:
    """Strip stop-token tails, code fences, and trailing whitespace."""

    text = text.replace("<CURSOR/>", "")
    for stop in STOP_TOKENS:
        idx = text.find(stop)
        if idx != -1:
            text = text[:idx]
    # Strip enclosing fences if the model still emitted any.
    text = text.strip()
    if text.startswith("```"):
        first_newline = text.find("\n")
        if first_newline != -1:
            text = text[first_newline + 1 :]
        if text.endswith("```"):
            text = text[:-3]
    return text.rstrip()
