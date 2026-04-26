"""Tests for the run.approve_plan JSON-RPC binding."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from thalyn_brain.approval_rpc import register_approval_methods
from thalyn_brain.chat import register_chat_methods
from thalyn_brain.orchestration import Runner
from thalyn_brain.provider import AnthropicProvider, ProviderRegistry
from thalyn_brain.rpc import Dispatcher

from tests.provider._fake_sdk import factory_for, result_message, text_message


def _registry_with(provider: AnthropicProvider) -> ProviderRegistry:
    registry = ProviderRegistry()
    registry._providers["anthropic"] = provider
    return registry


def _dispatcher_with(registry: ProviderRegistry, tmp_path: Path) -> tuple[Dispatcher, Runner]:
    runner = Runner(registry, data_dir=tmp_path)
    dispatcher = Dispatcher()
    register_chat_methods(dispatcher, registry, runner=runner)
    register_approval_methods(dispatcher, runner)
    return dispatcher, runner


def _captured() -> tuple[list[tuple[str, Any]], Any]:
    captured: list[tuple[str, Any]] = []

    async def notify(method: str, params: Any) -> None:
        captured.append((method, params))

    return captured, notify


async def _start_run(
    dispatcher: Dispatcher,
    notify: Any,
    *,
    prompt: str = "Hi",
) -> str:
    response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "chat.send",
            "params": {
                "sessionId": "sess",
                "providerId": "anthropic",
                "prompt": prompt,
            },
        },
        notify,
    )
    assert response is not None
    run_id = response["result"]["runId"]
    assert isinstance(run_id, str)
    return run_id


async def test_approve_plan_resumes_run_and_streams_chat_chunks(
    tmp_path: Path,
) -> None:
    _fake, factory = factory_for(
        [
            text_message('{"goal": "Hi", "steps": []}'),
            result_message(),
            text_message("Hello!"),
            result_message(),
        ]
    )
    provider = AnthropicProvider(client_factory=factory)
    registry = _registry_with(provider)
    dispatcher, _runner = _dispatcher_with(registry, tmp_path)

    captured, notify = _captured()
    run_id = await _start_run(dispatcher, notify)

    response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "run.approve_plan",
            "params": {
                "runId": run_id,
                "providerId": "anthropic",
                "decision": "approve",
            },
        },
        notify,
    )

    assert response is not None
    result = response["result"]
    assert result["runId"] == run_id
    assert result["status"] == "completed"
    assert result["finalResponse"] == "Hello!"

    chat_chunks = [params for method, params in captured if method == "chat.chunk"]
    assert any(p["chunk"]["kind"] == "text" for p in chat_chunks)


async def test_edit_decision_requires_edited_plan(tmp_path: Path) -> None:
    _fake, factory = factory_for([text_message('{"goal": "x", "steps": []}'), result_message()])
    provider = AnthropicProvider(client_factory=factory)
    registry = _registry_with(provider)
    dispatcher, _runner = _dispatcher_with(registry, tmp_path)

    _, notify = _captured()
    run_id = await _start_run(dispatcher, notify)

    response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "run.approve_plan",
            "params": {
                "runId": run_id,
                "providerId": "anthropic",
                "decision": "edit",
            },
        },
        notify,
    )
    assert response is not None
    assert response["error"]["code"] == -32602


async def test_edit_decision_propagates_edited_plan(tmp_path: Path) -> None:
    _fake, factory = factory_for(
        [
            text_message('{"goal": "original", "steps": []}'),
            result_message(),
            text_message("Done."),
            result_message(),
        ]
    )
    provider = AnthropicProvider(client_factory=factory)
    registry = _registry_with(provider)
    dispatcher, _runner = _dispatcher_with(registry, tmp_path)

    _, notify = _captured()
    run_id = await _start_run(dispatcher, notify)

    edited_plan = {
        "goal": "edited",
        "nodes": [
            {
                "id": "step_e",
                "order": 0,
                "description": "Edited step.",
                "rationale": "User-edited.",
                "estimatedCost": {},
                "status": "pending",
                "parentId": None,
            }
        ],
    }

    response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "run.approve_plan",
            "params": {
                "runId": run_id,
                "providerId": "anthropic",
                "decision": "edit",
                "editedPlan": edited_plan,
            },
        },
        notify,
    )
    assert response is not None
    assert response["result"]["plan"]["goal"] == "edited"


async def test_reject_decision_kills_the_run(tmp_path: Path) -> None:
    _fake, factory = factory_for([text_message('{"goal": "x", "steps": []}'), result_message()])
    provider = AnthropicProvider(client_factory=factory)
    registry = _registry_with(provider)
    dispatcher, _runner = _dispatcher_with(registry, tmp_path)

    _, notify = _captured()
    run_id = await _start_run(dispatcher, notify)

    response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "run.approve_plan",
            "params": {
                "runId": run_id,
                "providerId": "anthropic",
                "decision": "reject",
            },
        },
        notify,
    )

    assert response is not None
    assert response["result"]["status"] == "killed"
    assert response["result"]["finalResponse"] == ""


@pytest.mark.parametrize(
    "params, want_message",
    [
        ({"providerId": "p", "decision": "approve"}, "runId"),
        ({"runId": "r", "decision": "approve"}, "providerId"),
        ({"runId": "r", "providerId": "p"}, "decision"),
        ({"runId": "r", "providerId": "p", "decision": "maybe"}, "decision"),
    ],
)
async def test_param_validation(
    tmp_path: Path,
    params: dict[str, Any],
    want_message: str,
) -> None:
    registry = ProviderRegistry()
    dispatcher, _runner = _dispatcher_with(registry, tmp_path)
    _, notify = _captured()

    response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "run.approve_plan",
            "params": params,
        },
        notify,
    )

    assert response is not None
    assert response["error"]["code"] == -32602
    assert want_message in response["error"]["message"]


async def test_unknown_run_returns_invalid_params(tmp_path: Path) -> None:
    registry = ProviderRegistry()
    registry._providers["anthropic"] = AnthropicProvider()
    dispatcher, _runner = _dispatcher_with(registry, tmp_path)
    _, notify = _captured()

    response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "run.approve_plan",
            "params": {
                "runId": "r_does_not_exist",
                "providerId": "anthropic",
                "decision": "approve",
            },
        },
        notify,
    )

    assert response is not None
    assert response["error"]["code"] == -32602
    assert "no resumable run" in response["error"]["message"]
