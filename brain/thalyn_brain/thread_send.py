"""``thread.send`` — the v2 entry point for the eternal thread.

The handler enforces ADR-0022's invariants:

1. The user's ``THREAD_TURN`` lands with ``status='in_progress'``
   under ``synchronous=FULL`` *before* the brain emits the first
   ``thread.chunk`` ``start`` notification.
2. The brain's reply turn lands at the *completed* boundary —
   inserted in the same SQLite transaction that flips the user turn
   to ``completed``. A crash inside the transaction rolls both back;
   the user turn stays ``in_progress`` and the recovery prompt
   surfaces it on next launch.
3. Streaming chunks come out under ``thread.chunk`` rather than
   v1's ``chat.chunk`` so the renderer can subscribe to the eternal
   surface without reading legacy events.

For v0.21 thread.send is a chat-reply path: it calls the provider's
``stream_chat`` directly with the assembled per-turn context.
Planning + execution + critic + respond is the worker-run pipeline
(``chat.send`` / ``runner.run``) — chat replies don't need it.
The classify-and-route node that delegates project-scoped work to a
lead lands in v0.23 alongside the lead-as-first-class primitive.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any

from thalyn_brain.digest_runner import (
    maybe_compress_old_digests,
    maybe_run_idle_digest,
    run_digest,
)
from thalyn_brain.provider import (
    ChatChunk,
    ChatErrorChunk,
    ChatStartChunk,
    ChatStopChunk,
    ChatTextChunk,
    ChatToolCallChunk,
    ChatToolResultChunk,
    ProviderNotImplementedError,
    ProviderRegistry,
)
from thalyn_brain.rpc import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    Dispatcher,
    JsonValue,
    Notifier,
    RpcError,
    RpcParams,
)
from thalyn_brain.thread_context import AssembledContext, assemble_context
from thalyn_brain.threads import (
    ThreadsStore,
    ThreadTurn,
    new_turn_id,
)

THREAD_CHUNK = "thread.chunk"
DEFAULT_BRAIN_AGENT_ID = "agent_brain"


def register_thread_send_methods(
    dispatcher: Dispatcher,
    *,
    threads_store: ThreadsStore,
    registry: ProviderRegistry,
) -> None:
    """Register ``thread.send`` and the recovery helpers.

    The recovery methods are synchronous reads against the
    in-progress index — the renderer polls ``thread.recovery_status``
    on first connect to decide whether to surface the "your last
    message got cut off" prompt, then resolves it via
    ``thread.recovery_resolve``. (Notifications can't be emitted
    before the renderer attaches the stdio channel, so a poll is the
    right primitive.)
    """

    async def thread_send(params: RpcParams, notify: Notifier) -> JsonValue:
        return await _handle_thread_send(params, notify, threads_store, registry)

    async def thread_recovery_status(params: RpcParams) -> JsonValue:
        return await _handle_recovery_status(params, threads_store)

    async def thread_recovery_resolve(params: RpcParams) -> JsonValue:
        return await _handle_recovery_resolve(params, threads_store)

    async def digest_run(params: RpcParams) -> JsonValue:
        return await _handle_digest_run(params, threads_store, registry)

    dispatcher.register_streaming("thread.send", thread_send)
    dispatcher.register("thread.recovery_status", thread_recovery_status)
    dispatcher.register("thread.recovery_resolve", thread_recovery_resolve)
    dispatcher.register("digest.run", digest_run)


async def _handle_thread_send(
    params: RpcParams,
    notify: Notifier,
    store: ThreadsStore,
    registry: ProviderRegistry,
) -> JsonValue:
    thread_id = _require_str(params, "threadId")
    provider_id = _require_str(params, "providerId")
    prompt = _require_str(params, "prompt")
    project_id_value = params.get("projectId")
    project_id: str | None = project_id_value if isinstance(project_id_value, str) else None
    base_system_prompt_value = params.get("systemPrompt")
    base_system_prompt: str | None = (
        base_system_prompt_value if isinstance(base_system_prompt_value, str) else None
    )

    # 1. Verify the thread row exists. The default ``thread_self`` is
    # seeded by migration 004; tests / future code paths can name a
    # different thread as long as it's been created.
    thread = await store.get_thread(thread_id)
    if thread is None:
        raise RpcError(
            code=INVALID_PARAMS,
            message=f"thread {thread_id!r} not found",
        )

    # 2. Resolve the provider before we persist anything — a bad
    # provider id should error symmetrically with chat.send rather
    # than leaving an orphan in_progress turn behind.
    provider = registry.get(provider_id)

    # 3. Close out the prior session if the user has been idle past
    # the threshold. The digest summarises the prior window, so it
    # has to land before the new user turn lands — otherwise the new
    # turn slips into the next digest's window and the boundary is
    # smeared.
    await maybe_run_idle_digest(provider, store, thread_id=thread_id)
    await maybe_compress_old_digests(provider, store, thread_id=thread_id)

    # 4. Persist the user turn FIRST, status='in_progress' (ADR-0022 §1).
    now_ms = int(time.time() * 1000)
    user_turn = ThreadTurn(
        turn_id=new_turn_id(),
        thread_id=thread_id,
        project_id=project_id,
        agent_id=None,
        role="user",
        body=prompt,
        provenance=None,
        confidence=None,
        episodic_index_ptr=None,
        at_ms=now_ms,
        status="in_progress",
    )
    await store.begin_user_turn(user_turn)
    await store.touch_thread(thread_id, now_ms)

    # 5. Pre-compute the brain reply turn's id so streamed chunks can
    # reference it. The id flows back to the renderer in the response;
    # if the run errors mid-stream the renderer can still correlate
    # the partial chunks with a turn-shaped row that never lands.
    brain_turn_id = new_turn_id()

    # 6. Assemble the per-turn context bundle (rolling digest + recent
    # turns + conditional episodic recall) per §9.4.
    assembled = await assemble_context(
        store,
        thread_id=thread_id,
        user_message=prompt,
        base_system_prompt=base_system_prompt,
    )

    # 7. Stream the brain's reply chunk-by-chunk. Buffer text deltas
    # so the brain reply turn's body matches what the user saw.
    text_parts: list[str] = []
    error_message: str | None = None
    stop_reason: str | None = None
    try:
        chunks: AsyncIterator[ChatChunk] = provider.stream_chat(
            prompt,
            system_prompt=assembled.system_prompt,
        )
        async for chunk in chunks:
            wire = chunk.to_wire()
            await notify(THREAD_CHUNK, {"turnId": brain_turn_id, "chunk": wire})
            if isinstance(chunk, ChatTextChunk):
                text_parts.append(chunk.delta)
            elif isinstance(chunk, ChatErrorChunk):
                error_message = chunk.message
            elif isinstance(chunk, ChatStopChunk):
                stop_reason = chunk.reason
            elif isinstance(chunk, ChatStartChunk | ChatToolCallChunk | ChatToolResultChunk):
                # Forwarded above; nothing to fold into state for v0.21.
                pass
    except ProviderNotImplementedError as exc:
        # Provider missing isn't a class-A durability issue, but the
        # user turn stays in_progress so a corrected provider id on the
        # next attempt can still pick it up via the recovery flow.
        await notify(
            THREAD_CHUNK,
            {
                "turnId": brain_turn_id,
                "chunk": {"kind": "error", "message": str(exc)},
            },
        )
        raise RpcError(code=INVALID_PARAMS, message=str(exc)) from exc
    except Exception as exc:
        await notify(
            THREAD_CHUNK,
            {
                "turnId": brain_turn_id,
                "chunk": {"kind": "error", "message": str(exc)},
            },
        )
        raise RpcError(code=INTERNAL_ERROR, message=str(exc)) from exc

    # 8. If the provider surfaced an error chunk, leave the user turn
    # in_progress so recovery can replay. Surface the error to the
    # caller as INTERNAL_ERROR rather than swallowing it.
    if error_message is not None:
        raise RpcError(code=INTERNAL_ERROR, message=error_message)

    # 9. Persist the brain reply turn at the completed boundary,
    # atomically with flipping the user turn to completed (ADR-0022 §1).
    final_response = "".join(text_parts)
    brain_turn = ThreadTurn(
        turn_id=brain_turn_id,
        thread_id=thread_id,
        project_id=project_id,
        agent_id=DEFAULT_BRAIN_AGENT_ID,
        role="brain",
        body=final_response,
        provenance={
            "providerId": provider_id,
            "stopReason": stop_reason,
        },
        confidence=None,
        episodic_index_ptr=None,
        at_ms=int(time.time() * 1000),
        status="completed",
    )
    await store.complete_turn_pair(user_turn_id=user_turn.turn_id, brain_turn=brain_turn)
    await store.touch_thread(thread_id, brain_turn.at_ms)

    return {
        "threadId": thread_id,
        "userTurnId": user_turn.turn_id,
        "turnId": brain_turn_id,
        "agentId": DEFAULT_BRAIN_AGENT_ID,
        "projectId": project_id,
        "status": "completed",
        "finalResponse": final_response,
        "context": _context_summary(assembled),
    }


async def _handle_recovery_status(
    params: RpcParams,
    store: ThreadsStore,
) -> JsonValue:
    """Return any in-progress turns the renderer should surface on
    its next first-connect cycle. Empty list when nothing's parked."""
    thread_id = _require_str(params, "threadId")
    in_progress = await store.list_in_progress(thread_id)
    return {
        "threadId": thread_id,
        "inProgressTurns": [t.to_wire() for t in in_progress],
    }


async def _handle_digest_run(
    params: RpcParams,
    store: ThreadsStore,
    registry: ProviderRegistry,
) -> JsonValue:
    """Force-run the rolling summarizer.

    The architecture surfaces this as ``digest.run`` for the
    ``/wrap`` slash command and for tests; the same code path also
    runs at the 20-min idle gap inside ``thread.send``.
    """
    thread_id = _require_str(params, "threadId")
    provider_id = _require_str(params, "providerId")
    provider = registry.get(provider_id)
    digest = await run_digest(provider, store, thread_id=thread_id)
    return {
        "threadId": thread_id,
        "digest": digest.to_wire() if digest is not None else None,
    }


async def _handle_recovery_resolve(
    params: RpcParams,
    store: ThreadsStore,
) -> JsonValue:
    """User-driven resolution for an in-progress turn.

    ``decision`` is "discard" (drop the half-completed turn) or
    "retry" (drop the row and let the caller re-send the prompt).
    Returning whether the row was actually dropped lets the renderer
    detect a TOCTOU race against another concurrent recovery.
    """
    turn_id = _require_str(params, "turnId")
    decision = _require_str(params, "decision")
    if decision not in {"discard", "retry"}:
        raise RpcError(
            code=INVALID_PARAMS,
            message="decision must be 'discard' or 'retry'",
        )
    dropped = await store.abandon_in_progress(turn_id)
    return {"turnId": turn_id, "decision": decision, "dropped": dropped}


def _context_summary(assembled: AssembledContext) -> dict[str, Any]:
    """A compact view of what the assembler pulled into context.

    Surfaced in the ``thread.send`` response for telemetry and for
    the inspector pane that lands in v0.26. The renderer can show
    the user "Thalyn pulled in 3 historical turns and 1 digest"
    without re-reading the database.
    """
    return {
        "digestId": assembled.digest.digest_id if assembled.digest is not None else None,
        "recentTurnCount": len(assembled.recent_turns),
        "episodicHits": [
            {"turnId": h.turn.turn_id, "rank": h.rank} for h in assembled.episodic_hits
        ],
    }


def _require_str(params: RpcParams, key: str) -> str:
    value = params.get(key)
    if not isinstance(value, str) or not value:
        raise RpcError(
            code=INVALID_PARAMS,
            message=f"missing or non-string '{key}'",
        )
    return value
