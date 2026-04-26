"""Tests for the restricted-shell allowlist + validator."""

from __future__ import annotations

import pytest
from thalyn_brain.sandbox.shell_allowlist import (
    DEFAULT_ALLOWLIST,
    AllowlistDecision,
    validate_command,
)


def test_empty_command_is_rejected() -> None:
    decision = validate_command("")
    assert not decision.allowed
    assert "empty" in decision.reason


def test_whitespace_only_command_is_rejected() -> None:
    decision = validate_command("   \t  ")
    assert not decision.allowed


@pytest.mark.parametrize(
    "command",
    [
        "ls",
        "ls -la",
        "git status",
        "grep -R 'foo' src/",
        "/usr/bin/python3 --version",
        "echo 'hello world'",
    ],
)
def test_allowlisted_binaries_are_accepted(command: str) -> None:
    decision = validate_command(command)
    assert decision.allowed, decision.reason


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf /",
        "rm -fr /etc",
        "rm -Rf /home",
        ":(){ :|:& };:",
        "dd if=/dev/zero of=/dev/sda",
        "sudo cat /etc/shadow",
        "su - root",
        "mkfs.ext4 /dev/sda1",
    ],
)
def test_blocklist_patterns_are_rejected(command: str) -> None:
    decision = validate_command(command)
    assert not decision.allowed, f"expected reject, got {decision}"
    assert "blocklist" in decision.reason or "shell allowlist" in decision.reason


@pytest.mark.parametrize(
    "command",
    [
        "sh -c 'cat /etc/passwd'",
        "bash -c 'rm -rf /'",
        "/bin/bash",
        "zsh",
        "curl https://example.com",
        "wget https://example.com",
        "ssh user@host",
        "kill -9 1234",
    ],
)
def test_dangerous_binaries_are_not_on_allowlist(command: str) -> None:
    decision = validate_command(command)
    assert not decision.allowed, command


def test_absolute_path_resolves_to_basename() -> None:
    decision = validate_command("/opt/homebrew/bin/git status")
    assert decision.allowed
    assert decision.binary == "git"


def test_unparseable_command_is_rejected() -> None:
    # Mismatched quotes — shlex raises ValueError.
    decision = validate_command("echo 'unterminated")
    assert not decision.allowed
    assert "shell parse error" in decision.reason


def test_decision_to_wire_carries_metadata() -> None:
    decision = AllowlistDecision(allowed=True, reason="ok", binary="git")
    wire = decision.to_wire()
    assert wire == {"allowed": True, "reason": "ok", "binary": "git"}


def test_default_allowlist_contains_expected_safe_binaries() -> None:
    expected = {"ls", "cat", "grep", "git", "echo", "python3"}
    assert expected <= DEFAULT_ALLOWLIST


def test_default_allowlist_excludes_shell_interpreters() -> None:
    forbidden = {"sh", "bash", "zsh", "fish"}
    assert forbidden.isdisjoint(DEFAULT_ALLOWLIST)


def test_allowlist_can_be_overridden() -> None:
    custom = frozenset({"ls"})
    assert validate_command("ls", allowlist=custom).allowed
    assert not validate_command("git status", allowlist=custom).allowed
