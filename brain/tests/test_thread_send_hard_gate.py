"""End-to-end test for the hard-gate flow inside ``thread.send``.

The conversational path must not bypass per-action approval. When
the matcher hits a ``hard_gate=True`` action, ``thread.send`` stages
the inputs in ``PendingActionStore``, emits an
``action.approval_required`` notification, and replies with a "I'll
do this once you approve" turn — the executor stays parked until the
renderer calls ``action.approve``.

This is the F12.5 + F9.5 contract: hard-gate actions (publish, send
money, send messages on the user's behalf) still require per-action
approval, even when initiated via Thalyn.
"""

from __future__ import annotations

import re
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from thalyn_brain.action_registry import (
    Action,
    ActionInput,
    ActionMatch,
    ActionRegistry,
    ActionResult,
)
from thalyn_brain.action_rpc import register_action_methods
from thalyn_brain.agents import AgentRecordsStore
from thalyn_brain.pending_actions import PendingActionStore
from thalyn_brain.provider import AnthropicProvider, ProviderRegistry
from thalyn_brain.rpc import Dispatcher
from thalyn_brain.thread_send import register_thread_send_methods
from thalyn_brain.threads import Thread, ThreadsStore, new_thread_id

from tests.provider._fake_sdk import factory_for


def _now() -> int:
    return int(time.time() * 1000)


def _registry_with(provider: AnthropicProvider) -> ProviderRegistry:
    registry = ProviderRegistry()
    registry._providers["anthropic"] = provider
    return registry


async def _seed_thread(store: ThreadsStore) -> Thread:
    thread = Thread(
        thread_id=new_thread_id(),
        user_scope="self",
        created_at_ms=_now(),
        last_active_at_ms=_now(),
    )
    await store.insert_thread(thread)
    return thread


class _EmailMatcher:
    """Tiny matcher for the test fixture — recognises "email <addr> ..."
    phrasings and folds the recipient into the inputs."""

    _PATTERN = re.compile(
        r"^\s*(?:thalyn[,:\s]+)?email\s+(?P<to>\S+@\S+)\s+(?:saying|with|about)\s+(?P<body>.+?)\s*$",
        re.IGNORECASE,
    )

    def try_match(
        self,
        prompt: str,
        *,
        context: Mapping[str, Any],
    ) -> ActionMatch | None:
        match = self._PATTERN.match(prompt.strip())
        if match is None:
            return None
        return ActionMatch(
            action_name="email.send",
            inputs={"to": match.group("to"), "body": match.group("body").rstrip(".")},
            preview=f"Send an email to {match.group('to')}",
        )


def _build_registry_with_email() -> tuple[ActionRegistry, list[Mapping[str, Any]]]:
    sends: list[Mapping[str, Any]] = []

    async def email_send(inputs: Mapping[str, Any]) -> ActionResult:
        sends.append(dict(inputs))
        return ActionResult(
            confirmation=f"Sent email to {inputs['to']}.",
            followup={"deliveryId": "msg_42"},
        )

    registry = ActionRegistry()
    registry.register(
        Action(
            name="email.send",
            description="Send an email on the user's behalf.",
            inputs=(
                ActionInput(name="to", description="recipient"),
                ActionInput(name="body", description="message body"),
            ),
            executor=email_send,
            hard_gate=True,
            hard_gate_kind="external_send",
        )
    )
    registry.register_matcher(_EmailMatcher())
    return registry, sends


@pytest.mark.asyncio
async def test_thread_send_stages_hard_gate_and_skips_executor(tmp_path: Path) -> None:
    threads = ThreadsStore(data_dir=tmp_path)
    agents = AgentRecordsStore(data_dir=tmp_path)

    _fake, factory = factory_for([])  # No LLM calls expected on the gated path.
    provider = AnthropicProvider(client_factory=factory)
    provider_registry = _registry_with(provider)
    action_registry, sends = _build_registry_with_email()
    pending = PendingActionStore()

    dispatcher = Dispatcher()
    register_action_methods(
        dispatcher,
        registry=action_registry,
        pending_actions=pending,
    )
    register_thread_send_methods(
        dispatcher,
        threads_store=threads,
        registry=provider_registry,
        agent_records=agents,
        action_registry=action_registry,
        pending_actions=pending,
    )

    thread = await _seed_thread(threads)

    captured: list[tuple[str, Any]] = []

    async def notify(method: str, params: Any) -> None:
        captured.append((method, params))

    response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "thread.send",
            "params": {
                "threadId": thread.thread_id,
                "providerId": "anthropic",
                "prompt": "Thalyn, email alice@example.com saying shipping update.",
            },
        },
        notify,
    )
    assert response is not None
    result = response["result"]
    assert result["actionName"] == "email.send"
    assert "approval" in result["finalResponse"].lower()

    # The executor stayed parked.
    assert sends == []
    # And we emitted the approval-required event for the renderer.
    approval_events = [p for m, p in captured if m == "action.approval_required"]
    assert len(approval_events) == 1
    event = approval_events[0]
    assert event["actionName"] == "email.send"
    assert event["hardGateKind"] == "external_send"
    assert event["inputs"] == {
        "to": "alice@example.com",
        "body": "shipping update",
    }

    # action.approve resolves the gate and runs the executor.
    approval_id = event["approvalId"]
    approve_resp = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "action.approve",
            "params": {"approvalId": approval_id},
        },
        notify,
    )
    assert approve_resp is not None
    approve_result = approve_resp["result"]
    assert approve_result["status"] == "approved"
    assert sends == [{"to": "alice@example.com", "body": "shipping update"}]
    assert approve_result["followup"] == {"deliveryId": "msg_42"}


@pytest.mark.asyncio
async def test_thread_send_rejects_hard_gate_when_no_pending_store(tmp_path: Path) -> None:
    threads = ThreadsStore(data_dir=tmp_path)
    agents = AgentRecordsStore(data_dir=tmp_path)
    _fake, factory = factory_for([])
    provider = AnthropicProvider(client_factory=factory)
    provider_registry = _registry_with(provider)
    action_registry, _sends = _build_registry_with_email()

    dispatcher = Dispatcher()
    register_thread_send_methods(
        dispatcher,
        threads_store=threads,
        registry=provider_registry,
        agent_records=agents,
        action_registry=action_registry,
        # pending_actions intentionally omitted — a hard-gate hit
        # without a store should fail loud rather than silently
        # bypass the gate.
    )

    thread = await _seed_thread(threads)

    async def _noop(method: str, params: Any) -> None:
        return None

    response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "thread.send",
            "params": {
                "threadId": thread.thread_id,
                "providerId": "anthropic",
                "prompt": "Thalyn, email alice@example.com saying shipping update.",
            },
        },
        _noop,
    )
    assert response is not None
    assert "error" in response
