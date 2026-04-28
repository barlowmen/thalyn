"""Tests for the ``thread.send`` write path + recovery flow.

Covers the ADR-0022 invariants in IPC-shaped tests:

- The user turn lands ``in_progress`` before any chunk is emitted.
- The brain reply turn is inserted at completion, in the same
  transaction that flips the user turn — a crash inside the loop
  leaves the user turn ``in_progress`` and no brain row.
- ``thread.recovery_status`` and ``thread.recovery_resolve`` give
  the renderer the affordance the architecture's §9.5 mitigation
  table calls for.
- ``thread.send`` folds the rolling digest + recent turns into the
  system prompt the provider sees (context-assembly per §9.4).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from thalyn_brain.provider import AnthropicProvider, ProviderRegistry
from thalyn_brain.rpc import INVALID_PARAMS, Dispatcher
from thalyn_brain.thread_send import register_thread_send_methods
from thalyn_brain.threads import (
    SessionDigest,
    Thread,
    ThreadsStore,
    ThreadTurn,
    new_digest_id,
    new_thread_id,
    new_turn_id,
)
from thalyn_brain.threads_rpc import register_thread_methods

from tests.provider._fake_sdk import factory_for, result_message, text_message


def _now_ms() -> int:
    return int(time.time() * 1000)


def _registry_with(provider: AnthropicProvider) -> ProviderRegistry:
    registry = ProviderRegistry()
    registry._providers["anthropic"] = provider
    return registry


def _captured_notifier() -> tuple[list[tuple[str, Any]], Any]:
    captured: list[tuple[str, Any]] = []

    async def notify(method: str, params: Any) -> None:
        captured.append((method, params))

    return captured, notify


async def _seed_thread(store: ThreadsStore, thread_id: str | None = None) -> Thread:
    thread = Thread(
        thread_id=thread_id or new_thread_id(),
        user_scope="self",
        created_at_ms=_now_ms(),
        last_active_at_ms=_now_ms(),
    )
    await store.insert_thread(thread)
    return thread


async def _build_dispatcher(
    tmp_path: Path,
    *,
    provider_messages: list[Any] | None = None,
) -> tuple[Dispatcher, ThreadsStore, AnthropicProvider]:
    store = ThreadsStore(data_dir=tmp_path)
    messages = provider_messages or [text_message("done"), result_message()]
    _, factory = factory_for(messages)
    provider = AnthropicProvider(client_factory=factory)
    registry = _registry_with(provider)
    dispatcher = Dispatcher()
    register_thread_methods(dispatcher, store)
    register_thread_send_methods(
        dispatcher,
        threads_store=store,
        registry=registry,
    )
    return dispatcher, store, provider


# ---------------------------------------------------------------------------
# thread.send happy path
# ---------------------------------------------------------------------------


async def test_thread_send_persists_user_then_brain_turn(tmp_path: Path) -> None:
    dispatcher, store, _ = await _build_dispatcher(
        tmp_path,
        provider_messages=[text_message("auth refactor shipped"), result_message()],
    )
    thread = await _seed_thread(store)

    _, notify = _captured_notifier()
    response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "thread.send",
            "params": {
                "threadId": thread.thread_id,
                "providerId": "anthropic",
                "prompt": "what's the auth status",
            },
        },
        notify,
    )
    assert response is not None
    result = response["result"]
    assert result["status"] == "completed"
    assert result["finalResponse"] == "auth refactor shipped"
    assert result["agentId"] == "agent_brain"
    assert result["userTurnId"] != result["turnId"]

    # Both turns landed completed; user turn first by at_ms ordering.
    turns = await store.list_turns(thread.thread_id)
    assert [t.role for t in turns] == ["user", "brain"]
    assert turns[0].body == "what's the auth status"
    assert turns[1].body == "auth refactor shipped"
    assert all(t.status == "completed" for t in turns)
    assert turns[1].provenance is not None
    assert turns[1].provenance["providerId"] == "anthropic"

    # No in-progress rows after a clean turn.
    assert (await store.list_in_progress(thread.thread_id)) == []


async def test_thread_send_emits_thread_chunk_keyed_to_brain_turn(tmp_path: Path) -> None:
    dispatcher, store, _ = await _build_dispatcher(
        tmp_path,
        provider_messages=[text_message("hi"), result_message()],
    )
    thread = await _seed_thread(store)

    captured, notify = _captured_notifier()
    response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "thread.send",
            "params": {
                "threadId": thread.thread_id,
                "providerId": "anthropic",
                "prompt": "hi there",
            },
        },
        notify,
    )
    assert response is not None
    brain_turn_id = response["result"]["turnId"]
    chunk_events = [params for method, params in captured if method == "thread.chunk"]
    assert chunk_events, "thread.send did not emit any thread.chunk notifications"
    assert all(ev["turnId"] == brain_turn_id for ev in chunk_events)
    kinds = [ev["chunk"]["kind"] for ev in chunk_events]
    assert "start" in kinds
    assert "stop" in kinds
    # No legacy chat.chunk events on the v2 surface.
    assert not [m for m, _ in captured if m == "chat.chunk"]


async def test_thread_send_searchable_after_completion(tmp_path: Path) -> None:
    """A turn committed via thread.send must be findable through
    thread.search — the FTS triggers run inside complete_turn_pair's
    transaction (ADR-0022 + migration 006)."""
    dispatcher, store, _ = await _build_dispatcher(
        tmp_path,
        provider_messages=[text_message("the auth refactor is done"), result_message()],
    )
    thread = await _seed_thread(store)

    _, notify = _captured_notifier()
    await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "thread.send",
            "params": {
                "threadId": thread.thread_id,
                "providerId": "anthropic",
                "prompt": "tell me about the auth refactor",
            },
        },
        notify,
    )

    search_response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "thread.search",
            "params": {"threadId": thread.thread_id, "query": "auth refactor"},
        },
        notify,
    )
    assert search_response is not None
    hits = search_response["result"]["hits"]
    assert len(hits) >= 1
    assert any("auth refactor" in h["body"] for h in hits)


# ---------------------------------------------------------------------------
# thread.send recovery semantics
# ---------------------------------------------------------------------------


async def test_thread_send_unknown_thread_errors_invalid_params(tmp_path: Path) -> None:
    dispatcher, _, _ = await _build_dispatcher(tmp_path)
    _, notify = _captured_notifier()
    response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "thread.send",
            "params": {
                "threadId": "thread_does_not_exist",
                "providerId": "anthropic",
                "prompt": "hi",
            },
        },
        notify,
    )
    assert response is not None
    assert response["error"]["code"] == INVALID_PARAMS


async def test_thread_send_provider_failure_leaves_turn_in_progress(tmp_path: Path) -> None:
    """A provider that errors mid-stream leaves the user turn as
    in_progress so the recovery flow can pick it up."""

    class _FailingProvider:
        @property
        def id(self) -> str:
            return "anthropic"

        @property
        def display_name(self) -> str:
            return "Failing"

        @property
        def capability_profile(self) -> Any:
            raise NotImplementedError

        @property
        def default_model(self) -> str:
            return "fake"

        def supports(self, capability: Any) -> bool:
            return False

        async def stream_chat(self, prompt: str, **kwargs: Any) -> Any:
            yield None  # makes this an async generator
            raise RuntimeError("provider exploded")

    store = ThreadsStore(data_dir=tmp_path)
    registry = ProviderRegistry()
    registry._providers["anthropic"] = _FailingProvider()
    dispatcher = Dispatcher()
    register_thread_send_methods(
        dispatcher,
        threads_store=store,
        registry=registry,
    )
    thread = await _seed_thread(store)

    _, notify = _captured_notifier()
    response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "thread.send",
            "params": {
                "threadId": thread.thread_id,
                "providerId": "anthropic",
                "prompt": "this will fail",
            },
        },
        notify,
    )
    assert response is not None
    assert "error" in response

    in_progress = await store.list_in_progress(thread.thread_id)
    assert len(in_progress) == 1
    assert in_progress[0].body == "this will fail"


# ---------------------------------------------------------------------------
# thread.recovery_status / thread.recovery_resolve
# ---------------------------------------------------------------------------


async def test_recovery_status_returns_in_progress_turns(tmp_path: Path) -> None:
    dispatcher, store, _ = await _build_dispatcher(tmp_path)
    thread = await _seed_thread(store)
    pending = ThreadTurn(
        turn_id=new_turn_id(),
        thread_id=thread.thread_id,
        project_id=None,
        agent_id=None,
        role="user",
        body="i was typing this when the lights went out",
        provenance=None,
        confidence=None,
        episodic_index_ptr=None,
        at_ms=_now_ms(),
        status="in_progress",
    )
    await store.begin_user_turn(pending)

    _, notify = _captured_notifier()
    response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "thread.recovery_status",
            "params": {"threadId": thread.thread_id},
        },
        notify,
    )
    assert response is not None
    payload = response["result"]
    assert payload["threadId"] == thread.thread_id
    assert len(payload["inProgressTurns"]) == 1
    assert payload["inProgressTurns"][0]["body"].startswith("i was typing")


async def test_recovery_resolve_discard_drops_pending_turn(tmp_path: Path) -> None:
    dispatcher, store, _ = await _build_dispatcher(tmp_path)
    thread = await _seed_thread(store)
    pending = ThreadTurn(
        turn_id=new_turn_id(),
        thread_id=thread.thread_id,
        project_id=None,
        agent_id=None,
        role="user",
        body="discard me",
        provenance=None,
        confidence=None,
        episodic_index_ptr=None,
        at_ms=_now_ms(),
        status="in_progress",
    )
    await store.begin_user_turn(pending)

    _, notify = _captured_notifier()
    response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "thread.recovery_resolve",
            "params": {"turnId": pending.turn_id, "decision": "discard"},
        },
        notify,
    )
    assert response is not None
    assert response["result"]["dropped"] is True
    assert (await store.get_turn(pending.turn_id)) is None


async def test_recovery_resolve_rejects_unknown_decision(tmp_path: Path) -> None:
    dispatcher, _, _ = await _build_dispatcher(tmp_path)
    _, notify = _captured_notifier()
    response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "thread.recovery_resolve",
            "params": {"turnId": "turn_nope", "decision": "delete"},
        },
        notify,
    )
    assert response is not None
    assert response["error"]["code"] == INVALID_PARAMS


# ---------------------------------------------------------------------------
# context assembly — system prompt folds digest + recent + episodic
# ---------------------------------------------------------------------------


async def test_thread_send_folds_digest_and_recent_into_system_prompt(tmp_path: Path) -> None:
    """The provider's system_prompt should carry the rolling digest
    and the recent verbatim window (§9.4 steps 1-3)."""
    fake, factory = factory_for([text_message("OK"), result_message()])
    provider = AnthropicProvider(client_factory=factory)
    registry = _registry_with(provider)
    store = ThreadsStore(data_dir=tmp_path)
    dispatcher = Dispatcher()
    register_thread_send_methods(
        dispatcher,
        threads_store=store,
        registry=registry,
    )

    thread = await _seed_thread(store)
    # Seed two completed turns + a digest so context assembly has
    # something to fold.
    base = _now_ms()
    for i, body in enumerate(["hi from earlier", "still earlier"]):
        await store.insert_turn(
            ThreadTurn(
                turn_id=new_turn_id(),
                thread_id=thread.thread_id,
                project_id=None,
                agent_id=None,
                role="user" if i % 2 == 0 else "brain",
                body=body,
                provenance=None,
                confidence=None,
                episodic_index_ptr=None,
                at_ms=base + i,
                status="completed",
            )
        )
    await store.insert_digest(
        SessionDigest(
            digest_id=new_digest_id(),
            thread_id=thread.thread_id,
            window_start_ms=base,
            window_end_ms=base + 2,
            structured_summary={
                "topics": ["onboarding", "auth"],
                "decisions": [],
                "open_threads": [],
            },
            second_level_summary_of=None,
        )
    )

    _, notify = _captured_notifier()
    await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "thread.send",
            "params": {
                "threadId": thread.thread_id,
                "providerId": "anthropic",
                "prompt": "next question",
                "systemPrompt": "You are Thalyn.",
            },
        },
        notify,
    )

    sys_prompt = fake.options.system_prompt if fake.options is not None else ""
    assert sys_prompt is not None
    assert "You are Thalyn." in sys_prompt
    assert "Session digest" in sys_prompt
    assert "Topics: onboarding, auth" in sys_prompt
    assert "Recent conversation" in sys_prompt
    assert "hi from earlier" in sys_prompt
    assert "still earlier" in sys_prompt


async def test_thread_send_returns_context_summary(tmp_path: Path) -> None:
    dispatcher, store, _ = await _build_dispatcher(
        tmp_path,
        provider_messages=[text_message("ack"), result_message()],
    )
    thread = await _seed_thread(store)
    base = _now_ms()
    for i in range(3):
        await store.insert_turn(
            ThreadTurn(
                turn_id=new_turn_id(),
                thread_id=thread.thread_id,
                project_id=None,
                agent_id=None,
                role="user",
                body=f"earlier {i}",
                provenance=None,
                confidence=None,
                episodic_index_ptr=None,
                at_ms=base + i,
                status="completed",
            )
        )

    _, notify = _captured_notifier()
    response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "thread.send",
            "params": {
                "threadId": thread.thread_id,
                "providerId": "anthropic",
                "prompt": "follow up",
            },
        },
        notify,
    )
    assert response is not None
    summary = response["result"]["context"]
    assert summary["recentTurnCount"] == 3
    assert summary["digestId"] is None
    assert summary["episodicHits"] == []
