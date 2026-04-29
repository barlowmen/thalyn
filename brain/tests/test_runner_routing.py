"""End-to-end test for spawn-time routing through the worker router.

The runner must consult ``WorkerRouter.route`` per spawned worker
(per ADR-0023): the routed provider drives the child run, the
decision lands in the per-run audit log, and a ``run.routing_decision``
notification carries the same payload to the renderer. The
``local_only`` belt-and-braces refusal is exercised by routing a
cloud provider into a project flagged ``local_only`` and asserting
the spawn is skipped with the audit-log explanation.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from thalyn_brain.approval_rpc import register_approval_methods
from thalyn_brain.chat import register_chat_methods
from thalyn_brain.orchestration import Runner
from thalyn_brain.orchestration.state import RunStatus
from thalyn_brain.provider import AnthropicProvider, ProviderRegistry
from thalyn_brain.routing_table import (
    LocalOnlyViolation,
    MatchedRule,
    RouteDecision,
)
from thalyn_brain.rpc import Dispatcher
from thalyn_brain.runs import RunsStore
from thalyn_brain.runs_rpc import register_runs_methods

from tests.provider._fake_sdk import factory_for, result_message, text_message


class _StubRouter:
    """Test double for ``WorkerRouter`` — returns a canned decision per call.

    Records every ``route`` call so the test can assert the right
    ``(task_tag, project_id)`` reached the router. Raising
    ``LocalOnlyViolation`` is a per-call opt-in via ``raise_on_call``.
    """

    def __init__(
        self,
        *,
        decision: RouteDecision,
        raise_on_call: bool = False,
    ) -> None:
        self._decision = decision
        self._raise = raise_on_call
        self.calls: list[dict[str, str | None]] = []

    async def route(
        self,
        *,
        task_tag: str | None,
        project_id: str | None,
    ) -> RouteDecision:
        self.calls.append({"task_tag": task_tag, "project_id": project_id})
        if self._raise:
            raise LocalOnlyViolation(
                f"provider {self._decision.provider_id!r} is not local; "
                f"project {project_id!r} is local_only"
            )
        return self._decision


def _registry_with(provider: AnthropicProvider) -> ProviderRegistry:
    registry = ProviderRegistry()
    registry._providers["anthropic"] = provider
    registry._providers["ollama"] = provider
    return registry


def _capture() -> tuple[list[tuple[str, Any]], Any]:
    captured: list[tuple[str, Any]] = []

    async def notify(method: str, params: Any) -> None:
        captured.append((method, params))

    return captured, notify


def _build_dispatcher(
    registry: ProviderRegistry,
    tmp_path: Path,
    *,
    router: _StubRouter | None = None,
) -> tuple[Dispatcher, RunsStore, Runner]:
    store = RunsStore(data_dir=tmp_path)
    runner = Runner(
        registry,
        runs_store=store,
        data_dir=tmp_path,
        worker_router=router,
    )
    dispatcher = Dispatcher()
    register_chat_methods(dispatcher, registry, runner=runner)
    register_approval_methods(dispatcher, runner)
    register_runs_methods(dispatcher, store, runner=runner)
    return dispatcher, store, runner


def _delegated_plan_with_tag(task_tag: str) -> str:
    return (
        '{"goal": "investigate prior art",'
        ' "steps": ['
        '   {"description": "Audit existing call sites.",'
        '    "rationale": "Need a full picture.",'
        '    "estimated_tokens": 800,'
        '    "subagent_kind": "research",'
        f'    "task_tag": "{task_tag}"' + "}"
        "]}"
    )


@pytest.mark.asyncio
async def test_spawn_consults_router_and_uses_routed_provider(
    tmp_path: Path,
) -> None:
    _fake, factory = factory_for(
        [
            # Parent plan: one delegated step tagged "coding".
            text_message(_delegated_plan_with_tag("coding")),
            result_message(),
            # Child plan: empty (fallback single-step).
            text_message('{"goal": "x", "steps": []}'),
            result_message(),
            # Child respond turn.
            text_message("Investigated the call sites."),
            result_message(total_cost_usd=0.0001),
            # Parent respond turn.
            text_message("Here's what the sub-agent found."),
            result_message(total_cost_usd=0.0002),
        ]
    )
    provider = AnthropicProvider(client_factory=factory)
    registry = _registry_with(provider)

    routed_decision = RouteDecision(
        provider_id="ollama",
        task_tag="coding",
        effective_tag="coding",
        matched=MatchedRule.OVERRIDE,
    )
    router = _StubRouter(decision=routed_decision)
    dispatcher, store, _runner = _build_dispatcher(registry, tmp_path, router=router)

    captured, notify = _capture()

    send_response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "chat.send",
            "params": {
                "sessionId": "sess",
                "providerId": "anthropic",
                "prompt": "Investigate.",
                "projectId": "proj_alpha",
            },
        },
        notify,
    )
    assert send_response is not None
    root_run_id = send_response["result"]["runId"]
    assert send_response["result"]["projectId"] == "proj_alpha"

    # Approve the plan; the spawn happens during the resumed graph.
    approve_response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "run.approve_plan",
            "params": {
                "runId": root_run_id,
                "providerId": "anthropic",
                "decision": "approve",
            },
        },
        notify,
    )
    assert approve_response is not None
    assert approve_response["result"]["status"] == RunStatus.COMPLETED.value

    # The router was called with the tagged task + the project id.
    assert router.calls == [{"task_tag": "coding", "project_id": "proj_alpha"}]

    # A run.routing_decision notification carried the audit payload.
    routing_notifications = [
        params for method, params in captured if method == "run.routing_decision"
    ]
    assert len(routing_notifications) == 1
    decision_payload = routing_notifications[0]
    assert decision_payload["action"] == "route_worker"
    assert decision_payload["taskTag"] == "coding"
    assert decision_payload["effectiveTag"] == "coding"
    assert decision_payload["providerId"] == "ollama"
    assert decision_payload["matched"] == "override"
    assert decision_payload["projectId"] == "proj_alpha"

    # The child run's header records the routed provider id.
    child_run_id = decision_payload["runId"]
    headers = {h.run_id: h for h in await store.list_runs()}
    assert headers[child_run_id].provider_id == "ollama"
    assert headers[child_run_id].project_id == "proj_alpha"


@pytest.mark.asyncio
async def test_spawn_records_routing_decision_in_audit_log(tmp_path: Path) -> None:
    _fake, factory = factory_for(
        [
            text_message(_delegated_plan_with_tag("coding")),
            result_message(),
            text_message('{"goal": "x", "steps": []}'),
            result_message(),
            text_message("Done."),
            result_message(total_cost_usd=0.0001),
            text_message("All good."),
            result_message(total_cost_usd=0.0002),
        ]
    )
    provider = AnthropicProvider(client_factory=factory)
    registry = _registry_with(provider)
    router = _StubRouter(
        decision=RouteDecision(
            provider_id="ollama",
            task_tag="coding",
            effective_tag="coding",
            matched=MatchedRule.OVERRIDE,
        ),
    )
    dispatcher, _store, _runner = _build_dispatcher(registry, tmp_path, router=router)
    _captured, notify = _capture()

    send = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "chat.send",
            "params": {
                "sessionId": "sess",
                "providerId": "anthropic",
                "prompt": "x",
                "projectId": "proj_alpha",
            },
        },
        notify,
    )
    assert send is not None
    await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "run.approve_plan",
            "params": {
                "runId": send["result"]["runId"],
                "providerId": "anthropic",
                "decision": "approve",
            },
        },
        notify,
    )

    # The routing decision is appended as a ``decision`` line to the
    # child's per-run audit log under runs/{run_id}.log.
    runs_dir = tmp_path / "runs"
    log_files = sorted(runs_dir.glob("*.log"))
    routing_lines: list[dict[str, Any]] = []
    for path in log_files:
        for raw in path.read_text().splitlines():
            entry = json.loads(raw)
            payload = entry.get("payload") or {}
            if entry.get("kind") == "decision" and payload.get("action") == "route_worker":
                routing_lines.append(entry)

    assert len(routing_lines) == 1
    payload = routing_lines[0]["payload"]
    assert payload["taskTag"] == "coding"
    assert payload["providerId"] == "ollama"
    assert payload["projectId"] == "proj_alpha"
    assert payload["matched"] == "override"


@pytest.mark.asyncio
async def test_local_only_violation_skips_spawn_and_records_failure(
    tmp_path: Path,
) -> None:
    """A cloud provider sneaking into a ``local_only`` project's spawn
    is caught by the belt-and-braces check; the spawn is reported as
    ``skipped`` and the audit log carries the explanation."""
    _fake, factory = factory_for(
        [
            text_message(_delegated_plan_with_tag("coding")),
            result_message(),
            # Parent respond turn after the spawn was skipped.
            text_message("Routing refused; nothing to report."),
            result_message(total_cost_usd=0.0001),
        ]
    )
    provider = AnthropicProvider(client_factory=factory)
    registry = _registry_with(provider)

    router = _StubRouter(
        decision=RouteDecision(
            provider_id="anthropic",
            task_tag="coding",
            effective_tag="coding",
            matched=MatchedRule.LOCAL_ONLY,
        ),
        raise_on_call=True,
    )
    dispatcher, store, _runner = _build_dispatcher(registry, tmp_path, router=router)

    captured, notify = _capture()
    send = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "chat.send",
            "params": {
                "sessionId": "sess",
                "providerId": "anthropic",
                "prompt": "x",
                "projectId": "proj_local",
            },
        },
        notify,
    )
    assert send is not None
    root_run_id = send["result"]["runId"]
    await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "run.approve_plan",
            "params": {
                "runId": root_run_id,
                "providerId": "anthropic",
                "decision": "approve",
            },
        },
        notify,
    )

    # The spawn never produced a child run row — only the parent is
    # in the store.
    headers = await store.list_runs()
    assert {h.run_id for h in headers} == {root_run_id}

    # The renderer saw a routing-decision notification carrying the
    # ``local_only_violation`` error so the inspector can surface it.
    failures = [
        params
        for method, params in captured
        if method == "run.routing_decision" and params.get("error") == "local_only_violation"
    ]
    assert len(failures) == 1
    assert "local_only" in failures[0]["message"]
