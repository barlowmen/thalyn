"""Memory-write actions for the action registry.

"Thalyn, remember that I prefer atomic commits" is the canonical
phrasing. The matcher recognises the imperative; the executor lands
a ``personal``-scope ``preference`` entry in ``MemoryStore`` so it
surfaces back via the personal-memory recall layer the next time the
user references a related token.

The matcher captures the *content* — the part after "remember that"
— and feeds it to the executor as ``body``. Phrases without that
shape return ``None`` so the regular reply flow runs.

Scope/kind defaults reflect what the memory-recall path needs to
surface this naturally: ``personal`` so it crosses project
boundaries, ``preference`` so it shows up alongside other "how I
like to work" rows in the inspector.
"""

from __future__ import annotations

import re
import time
from collections.abc import Mapping
from typing import Any

from thalyn_brain.action_registry import (
    Action,
    ActionInput,
    ActionMatch,
    ActionRegistry,
    ActionResult,
)
from thalyn_brain.memory import MemoryEntry, MemoryStore, new_memory_id

MEMORY_REMEMBER_ACTION = "memory.remember"

# "remember (that)? <body>" — captures the body, strips a leading
# "that" / "Thalyn," / quoted markers. The ``Thalyn,?`` prefix is
# optional so the matcher fires whether the user addresses Thalyn
# explicitly or not.
_REMEMBER = re.compile(
    r"""
    ^\s*
    (?:thalyn[,:\s]+)?                  # optional "Thalyn, "
    (?:please\s+)?
    remember
    (?:\s+that)?                        # optional "that"
    [,:\s]+
    (?P<body>.+?)
    \s*$
    """,
    re.IGNORECASE | re.VERBOSE | re.DOTALL,
)


class MemoryRememberMatcher:
    """Matches imperative "remember …" phrasings.

    Folds the captured body into ``ActionMatch.inputs["body"]``. The
    matcher is intentionally narrow: it only recognises the
    sentence-leading imperative ("Thalyn, remember that I prefer
    atomic commits"), not embedded mentions of the word
    ``remember`` inside a longer reply.
    """

    def try_match(
        self,
        prompt: str,
        *,
        context: Mapping[str, Any],
    ) -> ActionMatch | None:
        match = _REMEMBER.match(prompt.strip())
        if match is None:
            return None
        body = match.group("body").strip()
        # Strip terminal punctuation that's part of the imperative,
        # not part of the memory itself.
        body = body.rstrip(".!?;").strip()
        if not body:
            return None
        return ActionMatch(
            action_name=MEMORY_REMEMBER_ACTION,
            inputs={"body": body},
            preview=f"Remember: {body}",
        )


def register_memory_actions(
    registry: ActionRegistry,
    *,
    memory_store: MemoryStore,
    default_author: str = "thalyn",
) -> None:
    """Register the memory-write actions + matcher on ``registry``.

    ``default_author`` is the row's ``author`` field — Thalyn writing
    on the user's behalf carries the brain's id rather than the
    user's so the inspector can attribute the row to the
    conversational path.
    """

    async def remember(inputs: Mapping[str, Any]) -> ActionResult:
        body = str(inputs["body"]).strip()
        if not body:
            return ActionResult(
                confirmation=(
                    "I didn't catch what you wanted me to remember — try "
                    "'remember that I prefer atomic commits'."
                )
            )
        now_ms = int(time.time() * 1000)
        entry = MemoryEntry(
            memory_id=new_memory_id(),
            project_id=None,
            scope="personal",
            kind="preference",
            body=body,
            author=default_author,
            created_at_ms=now_ms,
            updated_at_ms=now_ms,
        )
        await memory_store.insert(entry)
        return ActionResult(
            confirmation=f"Saved to personal memory: {body}",
            followup={"memoryId": entry.memory_id},
        )

    registry.register(
        Action(
            name=MEMORY_REMEMBER_ACTION,
            description=(
                "Save a personal-scope preference or fact so it surfaces in "
                "future turns across every project (e.g. 'remember that I "
                "prefer atomic commits')."
            ),
            inputs=(
                ActionInput(
                    name="body",
                    description="What to remember, in the user's own words.",
                    kind="memory_body",
                ),
            ),
            executor=remember,
        )
    )
    registry.register_matcher(MemoryRememberMatcher())


__all__ = [
    "MEMORY_REMEMBER_ACTION",
    "MemoryRememberMatcher",
    "register_memory_actions",
]
