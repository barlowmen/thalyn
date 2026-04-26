"""Sub-agent spawner contract.

The execute node delegates a plan step to a focused worker by calling
into a ``SubAgentSpawner``. The spawner is owned by the runner — that
is where the per-run state, audit log, and runs-index plumbing live.
The graph layer only sees the protocol so it can stay decoupled from
persistence concerns.
"""

from __future__ import annotations

from typing import Any, Protocol

from thalyn_brain.orchestration.state import SubAgentResult


class SubAgentSpawner(Protocol):
    """Async callable that drives one sub-agent run to completion."""

    async def __call__(
        self,
        *,
        parent_run_id: str,
        plan_node: dict[str, Any],
        depth: int,
    ) -> SubAgentResult: ...


__all__ = ["SubAgentResult", "SubAgentSpawner"]
