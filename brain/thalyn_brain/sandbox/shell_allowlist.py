"""Allowlist enforcement for sub-agent shell calls.

Sub-agents reach the host shell only through the ``restricted_shell``
tool, which calls into the validator below. Two layers:

1. **Binary allowlist.** The first token of the parsed command must be
   one of a known-safe set (file readers, text processors, git, the
   language runtimes we ship). Shell interpreters (``sh``, ``bash``)
   are *not* on the list — accepting ``sh -c <anything>`` would defeat
   the allowlist entirely.
2. **Pattern blocklist.** A short list of catastrophic-shape commands
   (``rm -rf /``, fork bombs, ``dd if=`` to a device, …) that we
   reject even when the binary itself is allowlisted.

Pulled out of ``restricted_shell.py`` so the canonical lists are
unit-testable on their own and so a tightening / loosening tweak only
touches the data, not the call path.
"""

from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass

DEFAULT_ALLOWLIST: frozenset[str] = frozenset(
    {
        # File reading and listing
        "cat",
        "head",
        "tail",
        "ls",
        "find",
        "tree",
        "stat",
        "file",
        "wc",
        "du",
        "df",
        # Text processing — read-only by default
        "grep",
        "rg",
        "egrep",
        "fgrep",
        "awk",
        "sed",
        "sort",
        "uniq",
        "cut",
        "tr",
        "diff",
        # Path / process inspection
        "pwd",
        "basename",
        "dirname",
        "which",
        "type",
        "env",
        "id",
        "whoami",
        "ps",
        "uname",
        "hostname",
        # Output
        "echo",
        "printf",
        "true",
        "false",
        # Git — worktree confines writes; --network=none blocks push
        "git",
        # Language runtimes — used by build / inspection sub-agents
        "python",
        "python3",
        "node",
        "deno",
        # Build tools
        "make",
        "cargo",
        "npm",
        "pnpm",
        "uv",
        "pip",
    }
)
"""Default set of binaries (basenames) sub-agents may invoke."""


DEFAULT_BLOCKLIST_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\brm\s+-[a-zA-Z]*[rR][a-zA-Z]*[fF]"),
    re.compile(r"\brm\s+-[a-zA-Z]*[fF][a-zA-Z]*[rR]"),
    re.compile(r"\bdd\s+.*\bof=/dev/"),
    re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:"),
    re.compile(r"\bmkfs(\.[a-z0-9]+)?\b"),
    re.compile(r"\b(?:sudo|su)\b"),
    re.compile(r"\bchmod\s+(?:\d{3,4}|[+-]?[ugo]?[+-=]?[rwxst]+)\s+/"),
    re.compile(r">\s*/dev/sd[a-z]"),
)
"""Patterns the raw command string is checked against before exec."""


@dataclass(frozen=True)
class AllowlistDecision:
    """Outcome of one allowlist check."""

    allowed: bool
    reason: str
    binary: str | None = None

    def to_wire(self) -> dict[str, object]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "binary": self.binary,
        }


def validate_command(
    command: str,
    *,
    allowlist: frozenset[str] = DEFAULT_ALLOWLIST,
    blocklist: tuple[re.Pattern[str], ...] = DEFAULT_BLOCKLIST_PATTERNS,
) -> AllowlistDecision:
    """Decide whether ``command`` may be dispatched into the sandbox.

    Reject when the parse fails, when the first token (basename) is
    not on the allowlist, or when any catastrophic-shape pattern
    matches the raw command string.
    """
    stripped = command.strip()
    if not stripped:
        return AllowlistDecision(allowed=False, reason="empty command")

    for pattern in blocklist:
        if pattern.search(stripped):
            return AllowlistDecision(
                allowed=False,
                reason=f"command matches the catastrophic-shape blocklist ({pattern.pattern!r})",
            )

    try:
        tokens = shlex.split(stripped, posix=True)
    except ValueError as exc:
        return AllowlistDecision(allowed=False, reason=f"shell parse error: {exc}")

    if not tokens:
        return AllowlistDecision(allowed=False, reason="no tokens after parse")

    # Take the first token's basename so absolute paths still match
    # the bare-name allowlist (`/usr/bin/git` ↔ `git`).
    binary = os.path.basename(tokens[0])

    if binary not in allowlist:
        return AllowlistDecision(
            allowed=False,
            reason=f"binary {binary!r} is not on the sub-agent shell allowlist",
            binary=binary,
        )

    return AllowlistDecision(allowed=True, reason="allowlisted", binary=binary)
