"""Sub-agent shell tool — runs commands through the allowlist.

Wraps a host-side ``subprocess`` call behind the validator from
``shell_allowlist``. The actual confinement (devcontainer + git
worktree + default-deny egress) lives in the Rust core; the brain
side is responsible for the pre-flight allowlist check and for
returning a structured ``RestrictedShellResult`` to the calling
sub-agent.

For v0.7 the tool runs against the host with the worktree as cwd,
which is what's reachable from the Python brain today. The
brain↔core reverse channel that lets this dispatch into the actual
container lands in a follow-up — at that point only the body of
``run_restricted_shell`` changes; the validator and the wire shape
stay put.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from thalyn_brain.sandbox.shell_allowlist import (
    AllowlistDecision,
    validate_command,
)


@dataclass(frozen=True)
class RestrictedShellResult:
    """Outcome of one ``run_restricted_shell`` invocation."""

    allowed: bool
    reason: str
    binary: str | None
    stdout: str
    stderr: str
    exit_code: int

    def to_wire(self) -> dict[str, object]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "binary": self.binary,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "exitCode": self.exit_code,
        }


async def run_restricted_shell(
    command: str,
    *,
    cwd: Path,
    timeout_seconds: float = 30.0,
) -> RestrictedShellResult:
    """Validate, then dispatch ``command`` against ``cwd``.

    Returns a structured result regardless of allow / reject so
    downstream agents can branch on ``allowed`` rather than catching
    exceptions. A rejected command never spawns a subprocess.
    """
    decision = validate_command(command)
    if not decision.allowed:
        return _from_rejection(decision)

    proc = await asyncio.create_subprocess_shell(
        command,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=timeout_seconds
        )
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return RestrictedShellResult(
            allowed=True,
            reason="timed out before completion",
            binary=decision.binary,
            stdout="",
            stderr=f"command exceeded {timeout_seconds}s timeout",
            exit_code=124,
        )
    return RestrictedShellResult(
        allowed=True,
        reason=decision.reason,
        binary=decision.binary,
        stdout=stdout_bytes.decode("utf-8", errors="replace"),
        stderr=stderr_bytes.decode("utf-8", errors="replace"),
        exit_code=proc.returncode if proc.returncode is not None else -1,
    )


def _from_rejection(decision: AllowlistDecision) -> RestrictedShellResult:
    return RestrictedShellResult(
        allowed=False,
        reason=decision.reason,
        binary=decision.binary,
        stdout="",
        stderr="",
        exit_code=126,  # POSIX "command invoked cannot execute"
    )
