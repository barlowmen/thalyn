"""Per-turn context assembly for the eternal thread.

Per ``02-architecture.md`` §9.4 the brain assembles a bounded working
context on every ``thread.send``: the system prompt, the rolling
digest, the recent verbatim turns, and (conditional) episodic recall
hits. Steps 4 (episodic) and 5 (project memory) are *pull-on-demand*
so a chatty session doesn't pay the cost on every turn — episodic
recall fires only when the user's input contains tokens that didn't
resolve in the recent window.

This module is the boundary the eternal thread folds into the existing
chat orchestration: callers ask for an ``AssembledContext`` and pass
its ``system_prompt`` field into the runner unchanged. Provider-side
prompt formatting stays the orchestration's problem.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from thalyn_brain.threads import (
    SessionDigest,
    ThreadsStore,
    ThreadTurn,
    ThreadTurnSearchHit,
)

DEFAULT_RECENT_LIMIT = 40
DEFAULT_EPISODIC_LIMIT = 3
EPISODIC_TOKEN_REGEX = re.compile(r"[A-Za-z][A-Za-z0-9_'-]{2,}")
# Tokens shorter than 3 chars or in this stop list don't earn an
# episodic-recall round-trip. The list is small on purpose — the goal
# is to drop "the / and / it" not to be a full English stopword set.
_EPISODIC_STOP_TOKENS = frozenset(
    {
        "the",
        "and",
        "but",
        "for",
        "with",
        "you",
        "are",
        "was",
        "have",
        "this",
        "that",
        "what",
        "when",
        "where",
        "how",
        "why",
        "did",
        "does",
        "can",
        "could",
        "would",
        "should",
        "your",
        "our",
        "their",
        "they",
        "them",
        "his",
        "her",
        "him",
        "she",
        "say",
        "said",
        "tell",
        "told",
        "ask",
        "asked",
        "from",
    }
)


@dataclass
class AssembledContext:
    """Everything ``thread.send`` needs to hand the runner.

    ``system_prompt`` is the assembled string (digest + recent +
    episodic hits + caller-supplied system prompt). The other fields
    are kept for telemetry and tests — the runner only reads
    ``system_prompt`` and ``user_message``.
    """

    system_prompt: str
    user_message: str
    digest: SessionDigest | None
    recent_turns: list[ThreadTurn]
    episodic_hits: list[ThreadTurnSearchHit] = field(default_factory=list)


async def assemble_context(
    store: ThreadsStore,
    *,
    thread_id: str,
    user_message: str,
    base_system_prompt: str | None = None,
    recent_limit: int = DEFAULT_RECENT_LIMIT,
    episodic_limit: int = DEFAULT_EPISODIC_LIMIT,
) -> AssembledContext:
    """Build the per-turn context bundle.

    The function reads three sources from the store: the latest
    rolling digest (``digest.latest``), the recent verbatim window
    (``thread.recent``-equivalent), and — when the user's message
    contains tokens that didn't appear in the recent window —
    a small episodic search to pull historical turns into context.
    """
    digest = await store.latest_digest(thread_id)
    recent_turns = await store.list_recent(
        thread_id,
        limit=recent_limit,
        include_in_progress=False,
    )

    # Decide whether episodic recall is worth a round-trip. We compare
    # the user's distinctive tokens against the recent window's body
    # text — anything that didn't show up recently is the candidate
    # search query. This is the §9.4 step-4 "conditional" rule.
    episodic_hits: list[ThreadTurnSearchHit] = []
    extra_query = _episodic_query_for(user_message, recent_turns)
    if extra_query:
        try:
            episodic_hits = await store.search_turns(
                extra_query,
                thread_id=thread_id,
                limit=episodic_limit,
                snippet=True,
            )
        except Exception:
            # FTS5 syntax errors should never break the user's turn —
            # fall back to no episodic recall.
            episodic_hits = []
        # Episodic hits that already appear in the recent window add no
        # new information; drop them.
        recent_ids = {t.turn_id for t in recent_turns}
        episodic_hits = [h for h in episodic_hits if h.turn.turn_id not in recent_ids]

    system_prompt = _render_system_prompt(
        base=base_system_prompt,
        digest=digest,
        recent_turns=recent_turns,
        episodic_hits=episodic_hits,
    )
    return AssembledContext(
        system_prompt=system_prompt,
        user_message=user_message,
        digest=digest,
        recent_turns=recent_turns,
        episodic_hits=episodic_hits,
    )


def _episodic_query_for(user_message: str, recent_turns: list[ThreadTurn]) -> str:
    """Return a query string for episodic recall, or empty if none.

    The heuristic: extract distinctive tokens from the user message,
    drop ones that appear in the recent window, drop short / common
    tokens. If two or more distinctive tokens survive, join them as
    the FTS query. Otherwise return empty (no episodic round-trip).
    """
    user_tokens = _tokens(user_message)
    if not user_tokens:
        return ""
    recent_text = " ".join(t.body for t in recent_turns)
    recent_token_set = _tokens(recent_text)
    candidates = [
        tok
        for tok in user_tokens
        if tok.lower() not in _EPISODIC_STOP_TOKENS
        and tok.lower() not in {r.lower() for r in recent_token_set}
    ]
    # Deduplicate while preserving order.
    seen: set[str] = set()
    distinctive: list[str] = []
    for tok in candidates:
        key = tok.lower()
        if key in seen:
            continue
        seen.add(key)
        distinctive.append(tok)
    if len(distinctive) < 2:
        return ""
    # Take the top 4 distinctive tokens. FTS5 treats space-separated
    # terms as an implicit AND, which is what we want for relevance.
    return " ".join(distinctive[:4])


def _tokens(text: str) -> list[str]:
    return EPISODIC_TOKEN_REGEX.findall(text)


def _render_system_prompt(
    *,
    base: str | None,
    digest: SessionDigest | None,
    recent_turns: list[ThreadTurn],
    episodic_hits: list[ThreadTurnSearchHit],
) -> str:
    """Compose the assembled system prompt as plain text.

    Section ordering matches §9.4: caller's base system prompt first
    (Thalyn identity), then the rolling digest, then the recent
    verbatim window, then episodic hits. Each section is omitted when
    empty so a fresh thread doesn't paste empty headers.
    """
    parts: list[str] = []
    if base:
        parts.append(base.rstrip())
    if digest is not None:
        parts.append("# Session digest (rolling summary)")
        parts.append(_format_digest(digest))
    if recent_turns:
        parts.append("# Recent conversation")
        parts.append(_format_recent(recent_turns))
    if episodic_hits:
        parts.append("# Earlier in the eternal thread")
        parts.append(_format_episodic(episodic_hits))
    return "\n\n".join(parts)


def _format_digest(digest: SessionDigest) -> str:
    summary = digest.structured_summary
    lines: list[str] = []
    topics = summary.get("topics") if isinstance(summary, dict) else None
    decisions = summary.get("decisions") if isinstance(summary, dict) else None
    open_threads = summary.get("open_threads") if isinstance(summary, dict) else None
    if isinstance(topics, list) and topics:
        lines.append("Topics: " + ", ".join(str(t) for t in topics))
    if isinstance(decisions, list) and decisions:
        lines.append("Decisions: " + ", ".join(str(d) for d in decisions))
    if isinstance(open_threads, list) and open_threads:
        lines.append("Open threads: " + ", ".join(str(o) for o in open_threads))
    if not lines:
        # Fall back to a stringified payload so a non-standard digest
        # shape still ends up in context rather than silently dropped.
        lines.append(str(summary))
    return "\n".join(lines)


def _format_recent(recent_turns: list[ThreadTurn]) -> str:
    lines: list[str] = []
    for turn in recent_turns:
        prefix = f"[{turn.role}]"
        lines.append(f"{prefix} {turn.body}")
    return "\n".join(lines)


def _format_episodic(hits: list[ThreadTurnSearchHit]) -> str:
    lines: list[str] = []
    for hit in hits:
        snippet = hit.snippet or hit.turn.body
        lines.append(f"[{hit.turn.role} @ turn {hit.turn.turn_id}] {snippet}")
    return "\n".join(lines)
