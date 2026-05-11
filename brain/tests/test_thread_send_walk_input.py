"""Test the walk-input pattern in ``thread.send``.

When the matcher recognises an intent but can't fill every required
input from the prompt alone, Thalyn must ask for the missing field
using the action's own schema description rather than failing
silently (per F9.5 schema-discovery).
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
from thalyn_brain.agents import AgentRecordsStore
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


class _ReadChannelMatcher:
    """Matches "read messages from <channel?>" — leaves ``channel``
    missing when the user didn't name one."""

    _PATTERN = re.compile(
        r"^\s*(?:thalyn[,:\s]+)?read\s+messages(?:\s+from\s+(?P<channel>\S+))?\s*[.!?]?\s*$",
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
        channel = match.group("channel")
        if channel:
            return ActionMatch(
                action_name="slack.read_channel",
                inputs={"channel": channel.rstrip(".!?").lstrip("#")},
            )
        return ActionMatch(
            action_name="slack.read_channel",
            inputs={},
            missing_inputs=("channel",),
            preview="Read recent Slack messages",
        )


def _build_registry() -> tuple[ActionRegistry, list[Mapping[str, Any]]]:
    reads: list[Mapping[str, Any]] = []

    async def read_channel(inputs: Mapping[str, Any]) -> ActionResult:
        reads.append(dict(inputs))
        return ActionResult(confirmation=f"Reading {inputs['channel']}")

    registry = ActionRegistry()
    registry.register(
        Action(
            name="slack.read_channel",
            description="Read recent messages from a Slack channel.",
            inputs=(
                ActionInput(
                    name="channel",
                    description="the Slack channel I should read from",
                    kind="string",
                ),
            ),
            executor=read_channel,
        )
    )
    registry.register_matcher(_ReadChannelMatcher())
    return registry, reads


@pytest.mark.asyncio
async def test_thread_send_asks_when_required_input_is_missing(tmp_path: Path) -> None:
    threads = ThreadsStore(data_dir=tmp_path)
    agents = AgentRecordsStore(data_dir=tmp_path)

    _fake, factory = factory_for([])  # No LLM calls — the matcher hit drives the reply.
    provider = AnthropicProvider(client_factory=factory)
    provider_registry = _registry_with(provider)
    action_registry, reads = _build_registry()

    dispatcher = Dispatcher()
    register_thread_send_methods(
        dispatcher,
        threads_store=threads,
        registry=provider_registry,
        agent_records=agents,
        action_registry=action_registry,
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
                "prompt": "Thalyn, read messages.",
            },
        },
        notify,
    )
    assert response is not None
    result = response["result"]
    assert result["actionName"] == "slack.read_channel"
    # The executor stayed parked; the reply was a question instead.
    assert reads == []
    reply = result["finalResponse"].lower()
    assert "channel" in reply
    assert "?" in reply
    # The followup payload tells the renderer which field is being
    # asked about — useful if a future GUI wants to prerender an
    # inline slot.
    followup_events = [params for method, params in captured if method == "action.followup"]
    assert len(followup_events) == 1
    assert followup_events[0]["followup"]["promptedFor"] == "channel"


@pytest.mark.asyncio
async def test_thread_send_executes_when_inputs_are_complete(tmp_path: Path) -> None:
    """Sanity check: when the matcher captures everything required,
    the walk-input branch must not fire."""
    threads = ThreadsStore(data_dir=tmp_path)
    agents = AgentRecordsStore(data_dir=tmp_path)
    _fake, factory = factory_for([])
    provider = AnthropicProvider(client_factory=factory)
    provider_registry = _registry_with(provider)
    action_registry, reads = _build_registry()

    dispatcher = Dispatcher()
    register_thread_send_methods(
        dispatcher,
        threads_store=threads,
        registry=provider_registry,
        agent_records=agents,
        action_registry=action_registry,
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
                "prompt": "Thalyn, read messages from #general.",
            },
        },
        _noop,
    )
    assert response is not None
    assert response["result"]["finalResponse"] == "Reading general"
    assert reads == [{"channel": "general"}]
