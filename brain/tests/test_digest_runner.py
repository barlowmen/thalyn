"""Tests for the rolling summarizer (digest_runner).

Covers ``run_digest``, the ``maybe_run_idle_digest`` 20-min trigger,
the second-level summarizer that compresses leaves into a parent,
and the ``digest.run`` IPC handler that surfaces the same code path
for the ``/wrap`` slash command and for tests.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from thalyn_brain.digest_runner import (
    DEFAULT_IDLE_THRESHOLD_MS,
    maybe_compress_old_digests,
    maybe_run_idle_digest,
    run_digest,
)
from thalyn_brain.provider import AnthropicProvider, ProviderRegistry
from thalyn_brain.rpc import Dispatcher
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

from tests.provider._fake_sdk import factory_for, result_message, text_message


def _now_ms() -> int:
    return int(time.time() * 1000)


async def _seed_thread(store: ThreadsStore) -> Thread:
    thread = Thread(
        thread_id=new_thread_id(),
        user_scope="self",
        created_at_ms=_now_ms(),
        last_active_at_ms=_now_ms(),
    )
    await store.insert_thread(thread)
    return thread


async def _add_completed_turn(
    store: ThreadsStore,
    thread_id: str,
    body: str,
    *,
    role: str = "user",
    at_ms: int,
) -> None:
    await store.insert_turn(
        ThreadTurn(
            turn_id=new_turn_id(),
            thread_id=thread_id,
            project_id=None,
            agent_id=None,
            role=role,
            body=body,
            provenance=None,
            confidence=None,
            episodic_index_ptr=None,
            at_ms=at_ms,
            status="completed",
        )
    )


def _registry_with(provider: AnthropicProvider) -> ProviderRegistry:
    registry = ProviderRegistry()
    registry._providers["anthropic"] = provider
    return registry


# ---------------------------------------------------------------------------
# run_digest — the basic summarizer
# ---------------------------------------------------------------------------


async def test_run_digest_writes_session_digest_row(tmp_path: Path) -> None:
    digest_json = (
        '{"topics": ["auth", "release"], "decisions": ["ship monday"], '
        '"open_threads": ["doc the rollback"]}'
    )
    _, factory = factory_for([text_message(digest_json), result_message()])
    provider = AnthropicProvider(client_factory=factory)
    store = ThreadsStore(data_dir=tmp_path)
    thread = await _seed_thread(store)
    # Use fixed past timestamps so until_ms (defaulted to now) is
    # comfortably ahead of every turn's at_ms.
    base = 1_000
    await _add_completed_turn(store, thread.thread_id, "auth refactor", at_ms=base)
    await _add_completed_turn(
        store, thread.thread_id, "shipped overnight", role="brain", at_ms=base + 5
    )

    digest = await run_digest(provider, store, thread_id=thread.thread_id)

    assert digest is not None
    assert digest.structured_summary["topics"] == ["auth", "release"]
    assert digest.structured_summary["decisions"] == ["ship monday"]
    persisted = await store.list_digests(thread.thread_id)
    assert len(persisted) == 1
    assert persisted[0].digest_id == digest.digest_id


async def test_run_digest_skips_when_too_few_unsummarized_turns(tmp_path: Path) -> None:
    _, factory = factory_for([text_message("{}"), result_message()])
    provider = AnthropicProvider(client_factory=factory)
    store = ThreadsStore(data_dir=tmp_path)
    thread = await _seed_thread(store)
    await _add_completed_turn(store, thread.thread_id, "alone", at_ms=1_000)
    digest = await run_digest(provider, store, thread_id=thread.thread_id)
    assert digest is None
    assert await store.list_digests(thread.thread_id) == []


async def test_run_digest_falls_back_when_model_emits_non_json(tmp_path: Path) -> None:
    """A model that ignores the JSON instructions must not break the
    durability path — the heuristic falls back to topic anchors from
    the user lines."""
    _, factory = factory_for([text_message("just some chatty prose"), result_message()])
    provider = AnthropicProvider(client_factory=factory)
    store = ThreadsStore(data_dir=tmp_path)
    thread = await _seed_thread(store)
    base = 1_000
    await _add_completed_turn(store, thread.thread_id, "what about onboarding?", at_ms=base)
    await _add_completed_turn(
        store, thread.thread_id, "we discussed it", role="brain", at_ms=base + 1
    )

    digest = await run_digest(provider, store, thread_id=thread.thread_id)
    assert digest is not None
    assert digest.structured_summary["topics"] == ["what about onboarding?"]


async def test_run_digest_window_starts_after_prior_digest(tmp_path: Path) -> None:
    """A digest's window starts at the last digest's window_end so
    no turn is summarised twice."""
    _, factory = factory_for(
        [
            text_message('{"topics": ["fresh"], "decisions": [], "open_threads": []}'),
            result_message(),
        ]
    )
    provider = AnthropicProvider(client_factory=factory)
    store = ThreadsStore(data_dir=tmp_path)
    thread = await _seed_thread(store)
    base = 1_000
    # Pre-existing digest covering 0..base.
    await store.insert_digest(
        SessionDigest(
            digest_id=new_digest_id(),
            thread_id=thread.thread_id,
            window_start_ms=0,
            window_end_ms=base,
            structured_summary={"topics": ["earlier"]},
            second_level_summary_of=None,
        )
    )
    # Add some prior turns the digest already covers, plus a fresh one.
    await _add_completed_turn(store, thread.thread_id, "old turn", at_ms=base - 10)
    await _add_completed_turn(store, thread.thread_id, "new question", at_ms=base + 10)
    await _add_completed_turn(store, thread.thread_id, "new answer", role="brain", at_ms=base + 20)

    digest = await run_digest(provider, store, thread_id=thread.thread_id)
    assert digest is not None
    # The fresh digest's window starts after the prior one's window_end.
    assert digest.window_start_ms == base + 10
    assert digest.window_end_ms == base + 20


# ---------------------------------------------------------------------------
# Idle-trigger
# ---------------------------------------------------------------------------


async def test_maybe_run_idle_digest_fires_after_threshold(tmp_path: Path) -> None:
    _, factory = factory_for(
        [
            text_message('{"topics": ["closed"], "decisions": [], "open_threads": []}'),
            result_message(),
        ]
    )
    provider = AnthropicProvider(client_factory=factory)
    store = ThreadsStore(data_dir=tmp_path)
    thread = await _seed_thread(store)
    base = 1_000
    await _add_completed_turn(store, thread.thread_id, "what happened?", at_ms=base)
    await _add_completed_turn(store, thread.thread_id, "nothing yet", role="brain", at_ms=base + 1)

    now = base + DEFAULT_IDLE_THRESHOLD_MS + 100
    digest = await maybe_run_idle_digest(provider, store, thread_id=thread.thread_id, now_ms=now)
    assert digest is not None
    assert digest.structured_summary["topics"] == ["closed"]


async def test_maybe_run_idle_digest_skips_when_active(tmp_path: Path) -> None:
    _, factory = factory_for([text_message("{}"), result_message()])
    provider = AnthropicProvider(client_factory=factory)
    store = ThreadsStore(data_dir=tmp_path)
    thread = await _seed_thread(store)
    base = 1_000
    await _add_completed_turn(store, thread.thread_id, "still chatting", at_ms=base)
    await _add_completed_turn(store, thread.thread_id, "responding", role="brain", at_ms=base + 1)

    # Only 5 minutes since the last turn — well under the 20-min threshold.
    now = base + 5 * 60 * 1000
    digest = await maybe_run_idle_digest(provider, store, thread_id=thread.thread_id, now_ms=now)
    assert digest is None
    assert await store.list_digests(thread.thread_id) == []


# ---------------------------------------------------------------------------
# Second-level summarizer
# ---------------------------------------------------------------------------


async def test_maybe_compress_old_digests_creates_parent(tmp_path: Path) -> None:
    """When the leaf-digest table grows past budget, the oldest batch
    rolls into a parent digest with each child's
    second_level_summary_of pointing back at the parent."""
    _, factory = factory_for(
        [
            text_message('{"topics": ["meta"], "decisions": ["ship it"], "open_threads": []}'),
            result_message(),
        ]
    )
    provider = AnthropicProvider(client_factory=factory)
    store = ThreadsStore(data_dir=tmp_path)
    thread = await _seed_thread(store)
    # 12 leaf digests, budget=10, batch_size=3 → compress the 3 oldest.
    base = 1_000
    digest_ids: list[str] = []
    for i in range(12):
        d = SessionDigest(
            digest_id=new_digest_id(),
            thread_id=thread.thread_id,
            window_start_ms=base + i,
            window_end_ms=base + i + 1,
            structured_summary={"topics": [f"window {i}"]},
            second_level_summary_of=None,
        )
        await store.insert_digest(d)
        digest_ids.append(d.digest_id)

    parent = await maybe_compress_old_digests(
        provider, store, thread_id=thread.thread_id, budget=10, batch_size=3
    )
    assert parent is not None
    assert parent.structured_summary["topics"] == ["meta"]

    # The three oldest digests now point at the parent.
    digests = await store.list_digests(thread.thread_id)
    children = {d.digest_id: d.second_level_summary_of for d in digests}
    for old_id in digest_ids[:3]:
        assert children[old_id] == parent.digest_id
    # Newer leaves stay unparented.
    for newer_id in digest_ids[3:]:
        assert children[newer_id] is None


async def test_maybe_compress_old_digests_skips_under_budget(tmp_path: Path) -> None:
    """Below budget there's nothing to compress — the call must return
    None and not invoke the provider."""

    class _AssertNotCalled:
        async def __aenter__(self) -> Any:
            raise AssertionError("provider should not be invoked")

        async def __aexit__(self, *exc: object) -> None:
            return None

    # We can't easily assert "not called" with the FakeClient, so use a
    # provider that explodes on use.
    fake, factory = factory_for([])
    provider = AnthropicProvider(client_factory=factory)
    store = ThreadsStore(data_dir=tmp_path)
    thread = await _seed_thread(store)
    for i in range(5):
        await store.insert_digest(
            SessionDigest(
                digest_id=new_digest_id(),
                thread_id=thread.thread_id,
                window_start_ms=i,
                window_end_ms=i + 1,
                structured_summary={"topics": []},
                second_level_summary_of=None,
            )
        )
    parent = await maybe_compress_old_digests(
        provider, store, thread_id=thread.thread_id, budget=10, batch_size=3
    )
    assert parent is None
    # FakeClient's queries list stays empty — provider was never called.
    assert fake.queries == []


# ---------------------------------------------------------------------------
# digest.run IPC
# ---------------------------------------------------------------------------


async def test_digest_run_ipc_writes_and_returns_digest(tmp_path: Path) -> None:
    _, factory = factory_for(
        [
            text_message('{"topics": ["wrap"], "decisions": [], "open_threads": []}'),
            result_message(),
        ]
    )
    provider = AnthropicProvider(client_factory=factory)
    registry = _registry_with(provider)
    store = ThreadsStore(data_dir=tmp_path)
    dispatcher = Dispatcher()
    register_thread_send_methods(dispatcher, threads_store=store, registry=registry)

    thread = await _seed_thread(store)
    base = 1_000
    await _add_completed_turn(store, thread.thread_id, "let's wrap", at_ms=base)
    await _add_completed_turn(store, thread.thread_id, "got it", role="brain", at_ms=base + 1)

    async def _drop(method: str, params: Any) -> None:
        return None

    response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "digest.run",
            "params": {"threadId": thread.thread_id, "providerId": "anthropic"},
        },
        _drop,
    )
    assert response is not None
    digest_payload = response["result"]["digest"]
    assert digest_payload is not None
    assert digest_payload["structuredSummary"]["topics"] == ["wrap"]


async def test_digest_run_ipc_returns_null_for_short_thread(tmp_path: Path) -> None:
    _, factory = factory_for([text_message("{}"), result_message()])
    provider = AnthropicProvider(client_factory=factory)
    registry = _registry_with(provider)
    store = ThreadsStore(data_dir=tmp_path)
    dispatcher = Dispatcher()
    register_thread_send_methods(dispatcher, threads_store=store, registry=registry)
    thread = await _seed_thread(store)
    # Only one completed turn — below MIN_TURNS_FOR_DIGEST.
    await _add_completed_turn(store, thread.thread_id, "alone", at_ms=1_000)

    async def _drop(method: str, params: Any) -> None:
        return None

    response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "digest.run",
            "params": {"threadId": thread.thread_id, "providerId": "anthropic"},
        },
        _drop,
    )
    assert response is not None
    assert response["result"]["digest"] is None
