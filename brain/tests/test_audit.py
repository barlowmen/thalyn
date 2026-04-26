"""Tests for the per-run audit log writer."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from thalyn_brain.audit import AuditLogWriter, wrap_notifier
from thalyn_brain.orchestration import Runner
from thalyn_brain.provider import AnthropicProvider, ProviderRegistry

from tests.provider._fake_sdk import factory_for, result_message, text_message


def _registry_with(provider: AnthropicProvider) -> ProviderRegistry:
    registry = ProviderRegistry()
    registry._providers["anthropic"] = provider
    return registry


def _read_audit(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def test_writer_appends_lines_in_order(tmp_path: Path) -> None:
    audit = AuditLogWriter("r_test", data_dir=tmp_path)
    audit.append("status", {"status": "planning"})
    audit.append("plan_update", {"plan": {"goal": "x", "nodes": []}})
    audit.append("approval", {"decision": "approve"})

    lines = _read_audit(audit.path)
    assert [line["kind"] for line in lines] == ["status", "plan_update", "approval"]
    assert all(line["runId"] == "r_test" for line in lines)
    assert all("ts" in line for line in lines)


async def test_wrap_notifier_writes_only_audit_relevant_methods(
    tmp_path: Path,
) -> None:
    audit = AuditLogWriter("r_wrap", data_dir=tmp_path)

    delivered: list[tuple[str, Any]] = []

    async def underlying(method: str, params: Any) -> None:
        delivered.append((method, params))

    teed = wrap_notifier(underlying, audit)
    await teed("run.status", {"status": "planning"})
    await teed("chat.chunk", {"chunk": {"kind": "text", "delta": "hi"}})
    await teed("run.action_log", {"entry": {"kind": "decision"}})

    # Underlying always sees every notification.
    assert [m for m, _ in delivered] == [
        "run.status",
        "chat.chunk",
        "run.action_log",
    ]

    # Audit log captures only the run.* methods, not chat.chunk.
    lines = _read_audit(audit.path)
    assert [line["kind"] for line in lines] == ["status", "action_log"]


async def test_runner_writes_audit_log_for_a_full_approve_flow(
    tmp_path: Path,
) -> None:
    _fake, factory = factory_for(
        [
            text_message('{"goal": "x", "steps": []}'),
            result_message(),
            text_message("done."),
            result_message(),
        ]
    )
    provider = AnthropicProvider(client_factory=factory)
    registry = _registry_with(provider)
    runner = Runner(registry, data_dir=tmp_path)

    async def notify(_method: str, _params: Any) -> None:
        return None

    paused = await runner.run(
        session_id="s",
        provider_id="anthropic",
        prompt="Hello",
        notify=notify,
    )
    await runner.approve_plan(
        run_id=paused.run_id,
        provider_id="anthropic",
        decision="approve",
        notify=notify,
    )

    log_path = tmp_path / "runs" / f"{paused.run_id}.log"
    assert log_path.exists()
    lines = _read_audit(log_path)
    kinds = [line["kind"] for line in lines]

    # The lifecycle includes status transitions (multiple), at least
    # one plan_update, one approval_required, the user's approval, and
    # action_log entries.
    assert "status" in kinds
    assert "plan_update" in kinds
    assert "approval_required" in kinds
    assert "approval" in kinds
    assert "action_log" in kinds

    # Find the approval entry — it carries the decision.
    approval_lines = [line for line in lines if line["kind"] == "approval"]
    assert any(line["payload"]["decision"] == "approve" for line in approval_lines)


async def test_runner_audit_log_records_reject(tmp_path: Path) -> None:
    _fake, factory = factory_for([text_message('{"goal": "x", "steps": []}'), result_message()])
    provider = AnthropicProvider(client_factory=factory)
    registry = _registry_with(provider)
    runner = Runner(registry, data_dir=tmp_path)

    async def notify(_method: str, _params: Any) -> None:
        return None

    paused = await runner.run(
        session_id="s",
        provider_id="anthropic",
        prompt="Hello",
        notify=notify,
    )
    await runner.approve_plan(
        run_id=paused.run_id,
        provider_id="anthropic",
        decision="reject",
        notify=notify,
    )

    log_path = tmp_path / "runs" / f"{paused.run_id}.log"
    lines = _read_audit(log_path)
    approval_lines = [line for line in lines if line["kind"] == "approval"]
    assert any(line["payload"]["decision"] == "reject" for line in approval_lines)
    statuses = [line["payload"]["status"] for line in lines if line["kind"] == "status"]
    assert "killed" in statuses
