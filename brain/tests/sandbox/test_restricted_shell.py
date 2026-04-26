"""Tests for the host-side restricted-shell dispatch path."""

from __future__ import annotations

from pathlib import Path

import pytest
from thalyn_brain.sandbox.restricted_shell import run_restricted_shell


async def test_rejected_command_returns_structured_failure(tmp_path: Path) -> None:
    result = await run_restricted_shell("rm -rf /", cwd=tmp_path)
    assert not result.allowed
    assert result.exit_code == 126
    assert result.stdout == ""
    assert result.stderr == ""
    assert "blocklist" in result.reason


async def test_allowed_command_runs_in_cwd(tmp_path: Path) -> None:
    (tmp_path / "alpha.txt").write_text("alpha")
    (tmp_path / "beta.txt").write_text("beta")

    result = await run_restricted_shell("ls", cwd=tmp_path)
    assert result.allowed
    assert result.exit_code == 0
    assert "alpha.txt" in result.stdout
    assert "beta.txt" in result.stdout
    assert result.binary == "ls"


async def test_allowed_command_can_fail_with_nonzero_exit(tmp_path: Path) -> None:
    # `cat` of a missing file is allowlisted but will exit non-zero.
    result = await run_restricted_shell("cat does-not-exist.txt", cwd=tmp_path)
    assert result.allowed
    assert result.exit_code != 0
    assert "does-not-exist.txt" in result.stderr.lower() or result.stderr != ""


async def test_timeout_kills_long_running_command(tmp_path: Path) -> None:
    # `sleep` isn't on the allowlist by default, so we can't use it
    # to exercise the timeout path directly. Use a python one-liner
    # — `python3` is allowlisted — to spin past the budget.
    result = await run_restricted_shell(
        "python3 -c 'import time; time.sleep(5)'",
        cwd=tmp_path,
        timeout_seconds=0.5,
    )
    assert result.allowed
    assert result.exit_code == 124
    assert "timeout" in result.stderr.lower()


async def test_to_wire_shape(tmp_path: Path) -> None:
    result = await run_restricted_shell("echo hi", cwd=tmp_path)
    wire = result.to_wire()
    assert wire["allowed"] is True
    assert wire["binary"] == "echo"
    assert wire["exitCode"] == 0
    assert "hi" in str(wire["stdout"])


@pytest.mark.parametrize("command", ["sh -c 'ls'", "bash"])
async def test_shell_interpreter_is_rejected(tmp_path: Path, command: str) -> None:
    result = await run_restricted_shell(command, cwd=tmp_path)
    assert not result.allowed
    assert result.exit_code == 126
