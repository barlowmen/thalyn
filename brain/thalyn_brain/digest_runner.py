"""Rolling summarizer for the eternal thread.

Per ``02-architecture.md`` §9.3 the summarizer runs at session
boundaries (a 20-min idle gap or an explicit ``/wrap``) and writes a
``SESSION_DIGEST`` row for the closing window. When the live digest
table grows past a budget a second-level summarizer compresses the
oldest leaves into a parent digest, with the parent's id stamped on
the children's ``second_level_summary_of`` column.

The summarizer talks to the active provider directly (no LangGraph
node) — this keeps the summarizer composable with the v2 graph the
classify-and-route phase will introduce in v0.23 without forcing
that graph's shape today.

The output is parsed permissively: a missing or malformed JSON
response falls back to a heuristic digest derived from the turns'
plain text so a flaky model can't take the eternal-thread tier
offline.
"""

from __future__ import annotations

import json
import re
import time

from thalyn_brain.provider import ChatTextChunk, LlmProvider
from thalyn_brain.threads import (
    SessionDigest,
    ThreadsStore,
    ThreadTurn,
    new_digest_id,
)

DEFAULT_IDLE_THRESHOLD_MS = 20 * 60 * 1000
DEFAULT_DIGEST_BUDGET = 40
DEFAULT_COMPRESS_BATCH = 10
MIN_TURNS_FOR_DIGEST = 2

DIGEST_SYSTEM_PROMPT = """You are the rolling summarizer for an eternal chat thread.

Read the conversation excerpt below and produce a structured summary.
Respond with a single JSON object matching this exact shape and nothing else:

{
  "topics": ["<short topic>", ...],
  "decisions": ["<decision the user / brain agreed on>", ...],
  "open_threads": ["<question or task left unresolved>", ...]
}

Keep each list to 5 entries or fewer. If a list is empty, return [].
Do not include any prose outside the JSON object.
"""

SECOND_LEVEL_SYSTEM_PROMPT = """You are compressing several rolling
summaries into a single higher-level summary.

Given the structured digests below (each in JSON), produce a single
JSON object of the same shape merging their contents. Deduplicate
topics, keep decisions in chronological order, and surface only the
open threads that are still open at the end of the most recent
digest. Respond with the JSON object only:

{
  "topics": ["..."],
  "decisions": ["..."],
  "open_threads": ["..."]
}
"""

_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def _now_ms() -> int:
    return int(time.time() * 1000)


async def run_digest(
    provider: LlmProvider,
    store: ThreadsStore,
    *,
    thread_id: str,
    until_ms: int | None = None,
) -> SessionDigest | None:
    """Summarize all completed turns since the last digest.

    Returns the new ``SessionDigest`` or ``None`` if there are fewer
    than ``MIN_TURNS_FOR_DIGEST`` un-summarized turns (a session that
    closes after one exchange isn't worth a digest of its own).
    """
    latest = await store.latest_digest(thread_id)
    window_start = latest.window_end_ms if latest is not None else 0
    window_end = until_ms if until_ms is not None else _now_ms()
    if window_end <= window_start:
        return None

    turns = await store.list_turns(thread_id)
    in_window = [
        t for t in turns if t.status == "completed" and window_start < t.at_ms <= window_end
    ]
    if len(in_window) < MIN_TURNS_FOR_DIGEST:
        return None

    summary = await _summarize_turns(provider, in_window)
    digest = SessionDigest(
        digest_id=new_digest_id(),
        thread_id=thread_id,
        window_start_ms=in_window[0].at_ms,
        window_end_ms=in_window[-1].at_ms,
        structured_summary=summary,
        second_level_summary_of=None,
    )
    await store.insert_digest(digest)
    return digest


async def maybe_run_idle_digest(
    provider: LlmProvider,
    store: ThreadsStore,
    *,
    thread_id: str,
    idle_threshold_ms: int = DEFAULT_IDLE_THRESHOLD_MS,
    now_ms: int | None = None,
) -> SessionDigest | None:
    """Run the summarizer when the gap before the next turn exceeds
    the idle threshold. Used by ``thread.send`` before the new user
    turn lands so the digest closes the prior session.
    """
    latest = await store.latest_digest(thread_id)
    cutoff = latest.window_end_ms if latest is not None else 0
    turns = await store.list_turns(thread_id)
    new_turns = [t for t in turns if t.status == "completed" and t.at_ms > cutoff]
    if len(new_turns) < MIN_TURNS_FOR_DIGEST:
        return None
    most_recent = max(t.at_ms for t in new_turns)
    now = now_ms if now_ms is not None else _now_ms()
    if (now - most_recent) < idle_threshold_ms:
        return None
    return await run_digest(provider, store, thread_id=thread_id, until_ms=most_recent)


async def maybe_compress_old_digests(
    provider: LlmProvider,
    store: ThreadsStore,
    *,
    thread_id: str,
    budget: int = DEFAULT_DIGEST_BUDGET,
    batch_size: int = DEFAULT_COMPRESS_BATCH,
) -> SessionDigest | None:
    """Second-level summarization when the leaf digest table grows
    past ``budget``. Compresses the oldest ``batch_size`` leaves into
    a parent digest and points each child's
    ``second_level_summary_of`` at the new parent.
    """
    if budget <= 0 or batch_size <= 1:
        return None
    leaves = await _list_leaf_digests(store, thread_id)
    if len(leaves) <= budget:
        return None
    batch = leaves[:batch_size]
    summary = await _summarize_digests(provider, batch)
    parent = SessionDigest(
        digest_id=new_digest_id(),
        thread_id=thread_id,
        window_start_ms=batch[0].window_start_ms,
        window_end_ms=batch[-1].window_end_ms,
        structured_summary=summary,
        second_level_summary_of=None,
    )
    await store.insert_digest(parent)
    for child in batch:
        await _set_parent_digest(store, child.digest_id, parent.digest_id)
    return parent


async def _summarize_turns(
    provider: LlmProvider,
    turns: list[ThreadTurn],
) -> dict[str, list[str]]:
    excerpt = "\n".join(f"[{t.role}] {t.body}" for t in turns)
    text = await _collect_text(provider, excerpt, system_prompt=DIGEST_SYSTEM_PROMPT)
    parsed = _parse_summary(text)
    if parsed is None:
        return _fallback_summary(turns)
    return parsed


async def _summarize_digests(
    provider: LlmProvider,
    digests: list[SessionDigest],
) -> dict[str, list[str]]:
    excerpt = "\n\n".join(json.dumps(d.structured_summary) for d in digests)
    text = await _collect_text(
        provider,
        excerpt,
        system_prompt=SECOND_LEVEL_SYSTEM_PROMPT,
    )
    parsed = _parse_summary(text)
    if parsed is None:
        # Fallback: union the leaves' fields and dedupe.
        return _fallback_merge(digests)
    return parsed


async def _collect_text(
    provider: LlmProvider,
    user_message: str,
    *,
    system_prompt: str,
) -> str:
    parts: list[str] = []
    async for chunk in provider.stream_chat(user_message, system_prompt=system_prompt):
        if isinstance(chunk, ChatTextChunk):
            parts.append(chunk.delta)
    return "".join(parts)


def _parse_summary(text: str) -> dict[str, list[str]] | None:
    match = _JSON_OBJECT_RE.search(text)
    if not match:
        return None
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return _normalize_summary(payload)


def _normalize_summary(payload: dict[str, object]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {"topics": [], "decisions": [], "open_threads": []}
    for key in out:
        value = payload.get(key)
        if isinstance(value, list):
            out[key] = [str(v) for v in value if isinstance(v, str | int | float)][:5]
    return out


def _fallback_summary(turns: list[ThreadTurn]) -> dict[str, list[str]]:
    """Heuristic digest when the model declines to produce JSON.

    Uses the most recent user turns as topic anchors so the next
    turn's context still has *something* to ground recall against.
    """
    user_lines = [t.body.strip() for t in turns if t.role == "user" and t.body.strip()]
    topics = [line[:80] for line in user_lines[-5:]]
    return {"topics": topics, "decisions": [], "open_threads": []}


def _fallback_merge(digests: list[SessionDigest]) -> dict[str, list[str]]:
    topics: list[str] = []
    decisions: list[str] = []
    open_threads: list[str] = []
    for d in digests:
        s = d.structured_summary
        if isinstance(s, dict):
            for key, target in (
                ("topics", topics),
                ("decisions", decisions),
                ("open_threads", open_threads),
            ):
                value = s.get(key)
                if isinstance(value, list):
                    target.extend(str(v) for v in value if isinstance(v, str | int | float))
    return {
        "topics": _dedupe(topics)[:5],
        "decisions": _dedupe(decisions)[:5],
        "open_threads": _dedupe(open_threads)[:5],
    }


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


async def _list_leaf_digests(store: ThreadsStore, thread_id: str) -> list[SessionDigest]:
    digests = await store.list_digests(thread_id)
    return [d for d in digests if d.second_level_summary_of is None]


async def _set_parent_digest(
    store: ThreadsStore,
    child_id: str,
    parent_id: str,
) -> None:
    """Update a child digest's second_level_summary_of pointer.

    The store doesn't expose this as a domain operation yet — we
    write the pointer directly through the connection helper since
    second-level compression is the only caller in v0.21.
    """
    import sqlite3

    def _sync() -> None:
        with sqlite3.connect(store._db_path, isolation_level=None) as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA synchronous = FULL")
            conn.execute(
                "UPDATE session_digests SET second_level_summary_of = ? WHERE digest_id = ?",
                (parent_id, child_id),
            )

    import asyncio

    await asyncio.to_thread(_sync)
