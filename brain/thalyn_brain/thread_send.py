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
from pathlib import Path
from typing import Any

from thalyn_brain.agents import AgentRecordsStore
from thalyn_brain.digest_runner import (
    maybe_compress_old_digests,
    maybe_run_idle_digest,
    run_digest,
)
from thalyn_brain.identity import THALYN_SYSTEM_PROMPT
from thalyn_brain.lead_delegation import (
    LEAD_INTRO_TEMPLATE,
    LEAD_REPLY_PREFIX_TEMPLATE,
    AddressedLead,
    SanityCheckVerdict,
    collect_lead_reply,
    evaluate_lead_escalation,
    find_addressed_lead,
    sanity_check_lead_reply,
)
from thalyn_brain.memory import MemoryStore
from thalyn_brain.project_classifier import (
    Classifier,
    classify_for_routing,
)
from thalyn_brain.project_context import ProjectContext, load_project_context
from thalyn_brain.projects import ProjectsStore
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
from thalyn_brain.routing_intents import RoutingActionsDispatcher
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
LEAD_ESCALATION = "lead.escalation"
DEFAULT_BRAIN_AGENT_ID = "agent_brain"


def register_thread_send_methods(
    dispatcher: Dispatcher,
    *,
    threads_store: ThreadsStore,
    registry: ProviderRegistry,
    agent_records: AgentRecordsStore | None = None,
    routing_actions: RoutingActionsDispatcher | None = None,
    memory_store: MemoryStore | None = None,
    projects_store: ProjectsStore | None = None,
    classifier: Classifier | None = None,
) -> None:
    """Register ``thread.send`` and the recovery helpers.

    The recovery methods are synchronous reads against the
    in-progress index — the renderer polls ``thread.recovery_status``
    on first connect to decide whether to surface the "your last
    message got cut off" prompt, then resolves it via
    ``thread.recovery_resolve``. (Notifications can't be emitted
    before the renderer attaches the stdio channel, so a poll is the
    right primitive.)

    ``agent_records`` is optional so the early v0.21 tests that don't
    exercise the lead path can keep their narrow setup. When the
    store is wired in, ``thread.send`` runs the lead-delegation
    classify-and-route step before assembling the brain's reply.

    ``routing_actions`` (per ADR-0023) is the action-registry stub for
    routing-edit intents. When wired, ``thread.send`` recognises
    phrases like "route coding to ollama in this project" before
    delegating, dispatches the action, and replies with the
    confirmation directly.

    ``memory_store`` enables personal-memory recall during context
    assembly: when a turn references tokens that didn't resolve in
    the recent window, the assembler fans out to ``personal``-scope
    rows so cross-project preferences surface back into context. The
    parameter is optional so legacy tests that only exercise the
    eternal-thread plumbing keep their narrow setup.

    ``projects_store`` lets the delegation path resolve the addressed
    lead's project so its ``THALYN.md`` (per F6.3) folds into the
    lead's system prompt at the moment of the hop. Optional for the
    same reason — narrow tests that don't drive a delegation flow
    keep their existing wiring.

    ``classifier`` (per F3.5) populates ``THREAD_TURN.project_id``
    when the renderer didn't supply a foreground project and the
    user didn't address a specific lead. Optional so single-project
    tests can keep the simpler shape; when omitted the foreground
    bias is the only signal and untagged turns stay untagged.
    """

    async def thread_send(params: RpcParams, notify: Notifier) -> JsonValue:
        return await _handle_thread_send(
            params,
            notify,
            threads_store,
            registry,
            agent_records,
            routing_actions,
            memory_store,
            projects_store,
            classifier,
        )

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
    agent_records: AgentRecordsStore | None,
    routing_actions: RoutingActionsDispatcher | None = None,
    memory_store: MemoryStore | None = None,
    projects_store: ProjectsStore | None = None,
    classifier: Classifier | None = None,
) -> JsonValue:
    thread_id = _require_str(params, "threadId")
    provider_id = _require_str(params, "providerId")
    prompt = _require_str(params, "prompt")
    project_id_value = params.get("projectId")
    foreground_project_id: str | None = (
        project_id_value if isinstance(project_id_value, str) and project_id_value else None
    )
    project_id: str | None = foreground_project_id
    base_system_prompt_value = params.get("systemPrompt")
    # Default to Thalyn's identity prompt when the caller doesn't supply
    # one. Per F1.2 the brain has a stable identity across every turn;
    # the renderer can override but the brain owns the default.
    base_system_prompt: str = (
        base_system_prompt_value
        if isinstance(base_system_prompt_value, str) and base_system_prompt_value
        else THALYN_SYSTEM_PROMPT
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

    # 4. Resolve the project the turn belongs to before persistence so
    # ``THREAD_TURN.project_id`` reflects the routing verdict. Routing
    # precedence (per F1.5 / F3.7):
    #   1. Explicit ``Lead-X, …`` address wins over both classifier and
    #      foreground — the user named the lead deliberately.
    #   2. Classifier verdict at ``threshold`` confidence overrides the
    #      foreground bias for messages that clearly reference another
    #      project.
    #   3. Foreground attention from the renderer is the sticky default.
    addressed = await _maybe_address_lead(prompt, agent_records)
    if addressed is not None:
        project_id = addressed.lead.project_id or project_id
    elif classifier is not None and projects_store is not None:
        active_projects = await projects_store.list_all(status="active")
        project_id = await classify_for_routing(
            classifier,
            prompt,
            active_projects,
            foreground_project_id=foreground_project_id,
        )

    # 5. Persist the user turn, status='in_progress' (ADR-0022 §1).
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
    if project_id is not None and projects_store is not None:
        # Stamp last-active so the switcher's recency sort sees this
        # project at the top after a routed turn lands.
        await projects_store.touch_active_at(project_id, now_ms)

    # 6. Pre-compute the brain reply turn's id so streamed chunks can
    # reference it. The id flows back to the renderer in the response;
    # if the run errors mid-stream the renderer can still correlate
    # the partial chunks with a turn-shaped row that never lands.
    brain_turn_id = new_turn_id()

    # 7. Assemble the per-turn context bundle (rolling digest + recent
    # turns + conditional episodic recall + personal-memory recall)
    # per §9.4 / F6.4 / F6.5.
    assembled = await assemble_context(
        store,
        thread_id=thread_id,
        user_message=prompt,
        base_system_prompt=base_system_prompt,
        memory_store=memory_store,
    )

    # 7a. Routing-edit intent (per ADR-0023). Recognise "route X to Y
    # in this project" before delegating; on a hit the action lands
    # against the per-project routing table and the brain's reply is
    # the dispatcher's confirmation. Misses fall through.
    if routing_actions is not None:
        routing_intent = await routing_actions.dispatch(prompt, project_id=project_id)
        if routing_intent is not None:
            return await _handle_routing_reply(
                notify=notify,
                store=store,
                provider_id=provider_id,
                thread_id=thread_id,
                user_turn=user_turn,
                project_id=project_id,
                brain_turn_id=brain_turn_id,
                confirmation=routing_intent.confirmation,
                action=routing_intent.action,
                assembled=assembled,
            )

    # 7b. Run the delegation flow when the user addressed an active
    # lead (resolved up at step 4 so the user-turn's project_id is
    # already aligned).
    if addressed is not None:
        project_context = await _load_lead_project_context(
            addressed.lead.project_id,
            projects_store,
        )
        return await _handle_delegated_reply(
            notify=notify,
            store=store,
            registry=registry,
            thread_id=thread_id,
            user_turn=user_turn,
            project_id=project_id,
            brain_turn_id=brain_turn_id,
            addressed=addressed,
            assembled=assembled,
            project_context=project_context,
        )

    # 8. Stream the brain's reply chunk-by-chunk. Buffer text deltas
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

    # 9. If the provider surfaced an error chunk, leave the user turn
    # in_progress so recovery can replay. Surface the error to the
    # caller as INTERNAL_ERROR rather than swallowing it.
    if error_message is not None:
        raise RpcError(code=INTERNAL_ERROR, message=error_message)

    # 10. Persist the brain reply turn at the completed boundary,
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


async def _maybe_address_lead(
    prompt: str,
    agent_records: AgentRecordsStore | None,
) -> AddressedLead | None:
    """Look up active leads (if the registry is wired) and check
    whether the user is addressing one.

    Returns ``None`` when no registry is configured, when the
    matcher finds no unambiguous match, or when no active lead exists.
    The caller falls back to a direct brain reply on ``None``.
    """
    if agent_records is None:
        return None
    leads = await agent_records.list_all(kind="lead", status="active")
    if not leads:
        return None
    return find_addressed_lead(prompt, leads)


async def _load_lead_project_context(
    project_id: str | None,
    projects_store: ProjectsStore | None,
) -> ProjectContext | None:
    """Resolve the lead's ``workspace_path`` into a ``ProjectContext``.

    Returns ``None`` when the project lookup yields no row, when the
    project has no workspace path, or when the workspace's
    ``THALYN.md`` / ``CLAUDE.md`` doesn't exist or doesn't parse.
    Errors are swallowed: a misconfigured workspace must never
    derail a delegation hop. F6.3 makes the project-context file a
    convenience tier, not a load-bearing one.
    """
    if project_id is None or projects_store is None:
        return None
    project = await projects_store.get(project_id)
    if project is None or not project.workspace_path:
        return None
    try:
        return load_project_context(Path(project.workspace_path))
    except OSError:
        return None


async def _handle_delegated_reply(
    *,
    notify: Notifier,
    store: ThreadsStore,
    registry: ProviderRegistry,
    thread_id: str,
    user_turn: ThreadTurn,
    project_id: str | None,
    brain_turn_id: str,
    addressed: AddressedLead,
    assembled: AssembledContext,
    project_context: ProjectContext | None = None,
) -> JsonValue:
    """Delegate the turn to a project lead and stream the reply.

    Persists three rows in one transaction: the user turn (flipped
    to completed), the lead's raw reply (``role='lead'``,
    ``agent_id=lead.agent_id``), and the brain's surfaced reply
    (``role='brain'``, ``agent_id=brain``, with provenance pointing
    at the lead's turn id). The renderer's drill-down (F1.10) then
    has a real source row to navigate to.
    """
    lead = addressed.lead
    lead_provider = registry.get(lead.default_provider_id)

    # Stream the start chunk immediately so the renderer reflects
    # activity, then surface the brain's preamble and the wrapped
    # lead reply as text deltas. The provider start chunk is the
    # brain's own — the lead's underlying call is silent on the wire.
    await notify(
        THREAD_CHUNK,
        {"turnId": brain_turn_id, "chunk": {"kind": "start", "model": "thalyn-relay"}},
    )

    intro_text = LEAD_INTRO_TEMPLATE.format(name=lead.display_name)
    await notify(
        THREAD_CHUNK,
        {"turnId": brain_turn_id, "chunk": {"kind": "text", "delta": intro_text}},
    )

    lead_reply, lead_error = await collect_lead_reply(
        lead_provider,
        lead=lead,
        user_message=addressed.body,
        project_context=project_context,
    )
    if lead_error is not None:
        # The lead's provider surfaced an error — leave the user turn
        # in_progress so the renderer can offer recovery, mirroring
        # the direct-reply error path's contract.
        await notify(
            THREAD_CHUNK,
            {
                "turnId": brain_turn_id,
                "chunk": {"kind": "error", "message": lead_error},
            },
        )
        raise RpcError(code=INTERNAL_ERROR, message=lead_error)

    verdict = sanity_check_lead_reply(lead_reply)
    wrapped = LEAD_REPLY_PREFIX_TEMPLATE.format(name=lead.display_name) + lead_reply
    if verdict.note is not None:
        wrapped = wrapped + "\n\n" + verdict.note
    delta = "\n\n" + wrapped
    await notify(
        THREAD_CHUNK,
        {"turnId": brain_turn_id, "chunk": {"kind": "text", "delta": delta}},
    )
    await notify(
        THREAD_CHUNK,
        {"turnId": brain_turn_id, "chunk": {"kind": "stop", "reason": "end_turn"}},
    )

    # F2.5 escalation: when the lead's reply is question-dense, surface
    # a "drop into Lead-X" CTA rather than relying on the user to read
    # 6 questions inline. ``evaluate_lead_escalation`` returns ``None``
    # for low-density replies so the relay path stays unchanged.
    escalation = evaluate_lead_escalation(lead, lead_reply)
    if escalation is not None:
        await notify(LEAD_ESCALATION, escalation.to_wire())

    now_ms = int(time.time() * 1000)
    lead_turn_id = new_turn_id()
    lead_turn = ThreadTurn(
        turn_id=lead_turn_id,
        thread_id=thread_id,
        project_id=lead.project_id or project_id,
        agent_id=lead.agent_id,
        role="lead",
        body=lead_reply,
        provenance={
            "leadId": lead.agent_id,
            "providerId": lead.default_provider_id,
        },
        confidence={"sanityCheck": _verdict_to_wire(verdict)},
        episodic_index_ptr=None,
        at_ms=now_ms,
        status="completed",
    )
    final_text = intro_text + delta
    brain_turn = ThreadTurn(
        turn_id=brain_turn_id,
        thread_id=thread_id,
        project_id=lead.project_id or project_id,
        agent_id=DEFAULT_BRAIN_AGENT_ID,
        role="brain",
        body=final_text,
        provenance={
            "delegatedTo": lead.agent_id,
            "leadDisplayName": lead.display_name,
            "leadTurnId": lead_turn_id,
        },
        confidence={"sanityCheck": _verdict_to_wire(verdict)},
        episodic_index_ptr=None,
        at_ms=now_ms,
        status="completed",
    )
    await store.complete_turn_pair(
        user_turn_id=user_turn.turn_id,
        brain_turn=brain_turn,
        extra_turns=[lead_turn],
    )
    await store.touch_thread(thread_id, now_ms)

    return {
        "threadId": thread_id,
        "userTurnId": user_turn.turn_id,
        "turnId": brain_turn_id,
        "agentId": DEFAULT_BRAIN_AGENT_ID,
        "projectId": lead.project_id or project_id,
        "status": "completed",
        "finalResponse": final_text,
        "context": _context_summary(assembled),
        "delegation": {
            "leadId": lead.agent_id,
            "leadTurnId": lead_turn_id,
            "leadDisplayName": lead.display_name,
            "sanityCheck": _verdict_to_wire(verdict),
        },
    }


async def _handle_routing_reply(
    *,
    notify: Notifier,
    store: ThreadsStore,
    provider_id: str,
    thread_id: str,
    user_turn: ThreadTurn,
    project_id: str | None,
    brain_turn_id: str,
    confirmation: str,
    action: str,
    assembled: AssembledContext,
) -> JsonValue:
    """Reply with the action dispatcher's confirmation text.

    The routing-edit action has already landed against the store by
    the time this runs; this turn's job is to surface the
    confirmation to the user in the eternal thread the same shape a
    direct brain reply would. Streamed as text deltas so the renderer
    sees the same chunk shape as a normal reply.
    """
    await notify(
        THREAD_CHUNK,
        {"turnId": brain_turn_id, "chunk": {"kind": "start", "model": "thalyn-routing"}},
    )
    await notify(
        THREAD_CHUNK,
        {"turnId": brain_turn_id, "chunk": {"kind": "text", "delta": confirmation}},
    )
    await notify(
        THREAD_CHUNK,
        {"turnId": brain_turn_id, "chunk": {"kind": "stop", "reason": "end_turn"}},
    )

    brain_turn = ThreadTurn(
        turn_id=brain_turn_id,
        thread_id=thread_id,
        project_id=project_id,
        agent_id=DEFAULT_BRAIN_AGENT_ID,
        role="brain",
        body=confirmation,
        provenance={
            "providerId": provider_id,
            "routingAction": action,
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
        "finalResponse": confirmation,
        "context": _context_summary(assembled),
        "routingAction": action,
    }


def _verdict_to_wire(verdict: SanityCheckVerdict) -> dict[str, Any]:
    return {"ok": verdict.ok, "note": verdict.note}


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
        "personalMemoryHits": [
            {"memoryId": entry.memory_id, "kind": entry.kind}
            for entry in assembled.personal_memory_hits
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
