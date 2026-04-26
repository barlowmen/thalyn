"""Tests for the inspect-run CLI.

These are sync tests so the CLI's `asyncio.run()` doesn't try to
nest inside pytest-asyncio's loop. Async seed work runs through a
local `asyncio.run()` before each call into `main()`.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from thalyn_brain.inspect_run import main
from thalyn_brain.orchestration import Runner
from thalyn_brain.provider import AnthropicProvider, ProviderRegistry
from thalyn_brain.runs import RunsStore

from tests.provider._fake_sdk import factory_for, result_message, text_message


def _registry_with(provider: AnthropicProvider) -> ProviderRegistry:
    registry = ProviderRegistry()
    registry._providers["anthropic"] = provider
    return registry


def _seed_run_sync(tmp_path: Path) -> str:
    """Drive a complete run synchronously and return its id."""

    async def seed() -> str:
        _fake, factory = factory_for([text_message('{"goal": "x", "steps": []}'), result_message()])
        provider = AnthropicProvider(client_factory=factory)
        registry = _registry_with(provider)
        store = RunsStore(data_dir=tmp_path)
        runner = Runner(registry, runs_store=store, data_dir=tmp_path)

        async def notify(_method: str, _params: Any) -> None:
            return None

        result = await runner.run(
            session_id="s",
            provider_id="anthropic",
            prompt="Hello there.",
            notify=notify,
        )
        return result.run_id

    return asyncio.run(seed())


def test_list_prints_seeded_runs(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _seed_run_sync(tmp_path)
    code = main(["--list", "--data-dir", str(tmp_path)])
    captured = capsys.readouterr()
    assert code == 0
    assert "Hello there." in captured.out


def test_list_json_emits_array(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run_id = _seed_run_sync(tmp_path)
    code = main(["--list", "--json", "--data-dir", str(tmp_path)])
    captured = capsys.readouterr()
    assert code == 0
    payload = json.loads(captured.out)
    assert isinstance(payload, list)
    assert any(item["runId"] == run_id for item in payload)


def test_show_prints_header_and_checkpoint(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run_id = _seed_run_sync(tmp_path)
    code = main([run_id, "--data-dir", str(tmp_path)])
    captured = capsys.readouterr()
    assert code == 0
    assert run_id in captured.out
    assert "Hello there." in captured.out
    assert "latest checkpoint" in captured.out


def test_show_unknown_run_returns_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "runs").mkdir(parents=True, exist_ok=True)
    code = main(["r_unknown", "--data-dir", str(tmp_path)])
    captured = capsys.readouterr()
    assert code == 1
    assert "run not found" in captured.err


def test_no_args_returns_usage_exit_code(tmp_path: Path) -> None:
    (tmp_path / "runs").mkdir(parents=True, exist_ok=True)
    code = main(["--data-dir", str(tmp_path)])
    assert code == 2
