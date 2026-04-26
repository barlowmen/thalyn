"""Per-run budget data + enforcement helpers.

Every run carries an optional budget — token / time / iteration caps
the user (or planner) sets at start. Each graph node updates
``BudgetConsumption`` and consults ``check_budget`` before
expensive work; any cap exceeded surfaces a budget gate and the
run halts.

Token tracking uses a length-based heuristic in v0.8 (chars / 4)
because the Claude Agent SDK's stream chunks don't expose per-chunk
token counts. The heuristic is conservative enough to land the
"halt at 5 k tokens" exit criterion; real token usage can plug in
later via ``stop`` chunk metadata.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Budget:
    """Caps for one run. Any field can be ``None`` to disable that
    dimension; an all-``None`` budget effectively skips enforcement."""

    max_tokens: int | None = None
    max_seconds: float | None = None
    max_iterations: int | None = None

    def to_wire(self) -> dict[str, Any]:
        return {
            "maxTokens": self.max_tokens,
            "maxSeconds": self.max_seconds,
            "maxIterations": self.max_iterations,
        }

    @staticmethod
    def from_wire(payload: Any) -> Budget | None:
        if not isinstance(payload, dict):
            return None
        return Budget(
            max_tokens=_int_or_none(payload.get("maxTokens")),
            max_seconds=_float_or_none(payload.get("maxSeconds")),
            max_iterations=_int_or_none(payload.get("maxIterations")),
        )

    def is_unbounded(self) -> bool:
        return self.max_tokens is None and self.max_seconds is None and self.max_iterations is None


@dataclass
class BudgetConsumption:
    """How much of the budget the run has spent so far."""

    tokens_used: int = 0
    elapsed_seconds: float = 0.0
    iterations: int = 0
    started_at_ms: int = 0

    def to_wire(self) -> dict[str, Any]:
        return {
            "tokensUsed": self.tokens_used,
            "elapsedSeconds": self.elapsed_seconds,
            "iterations": self.iterations,
            "startedAtMs": self.started_at_ms,
        }

    @staticmethod
    def from_wire(payload: Any) -> BudgetConsumption:
        if not isinstance(payload, dict):
            return BudgetConsumption()
        return BudgetConsumption(
            tokens_used=int(payload.get("tokensUsed") or 0),
            elapsed_seconds=float(payload.get("elapsedSeconds") or 0.0),
            iterations=int(payload.get("iterations") or 0),
            started_at_ms=int(payload.get("startedAtMs") or 0),
        )

    def with_iteration(self) -> BudgetConsumption:
        return BudgetConsumption(
            tokens_used=self.tokens_used,
            elapsed_seconds=self.elapsed_seconds,
            iterations=self.iterations + 1,
            started_at_ms=self.started_at_ms,
        )

    def with_tokens(self, delta: int) -> BudgetConsumption:
        return BudgetConsumption(
            tokens_used=self.tokens_used + max(0, delta),
            elapsed_seconds=self.elapsed_seconds,
            iterations=self.iterations,
            started_at_ms=self.started_at_ms,
        )

    def refresh_elapsed(self, now_ms: int | None = None) -> BudgetConsumption:
        now = now_ms if now_ms is not None else int(time.time() * 1000)
        elapsed = (now - self.started_at_ms) / 1000.0 if self.started_at_ms > 0 else 0.0
        return BudgetConsumption(
            tokens_used=self.tokens_used,
            elapsed_seconds=elapsed,
            iterations=self.iterations,
            started_at_ms=self.started_at_ms,
        )


@dataclass(frozen=True)
class BudgetCheck:
    """Outcome of one budget check."""

    exceeded: bool
    dimension: str | None = None
    limit: float | None = None
    actual: float | None = None

    @property
    def reason(self) -> str:
        if not self.exceeded:
            return "within budget"
        return f"{self.dimension} budget exceeded: {self.actual} > {self.limit}"

    def to_wire(self) -> dict[str, Any]:
        return {
            "exceeded": self.exceeded,
            "dimension": self.dimension,
            "limit": self.limit,
            "actual": self.actual,
            "reason": self.reason,
        }


def check_budget(
    budget: Budget | None,
    consumed: BudgetConsumption,
) -> BudgetCheck:
    """Decide whether ``consumed`` exceeds any of ``budget``'s caps.

    Token / iteration are checked exactly (``> max``); time is checked
    on the freshly-refreshed elapsed reading. Returns the first
    dimension that's over — the caller doesn't need a list because
    crossing one dimension is enough to halt.
    """
    if budget is None or budget.is_unbounded():
        return BudgetCheck(exceeded=False)

    refreshed = consumed.refresh_elapsed()

    if budget.max_iterations is not None and refreshed.iterations > budget.max_iterations:
        return BudgetCheck(
            exceeded=True,
            dimension="iterations",
            limit=float(budget.max_iterations),
            actual=float(refreshed.iterations),
        )
    if budget.max_seconds is not None and refreshed.elapsed_seconds > budget.max_seconds:
        return BudgetCheck(
            exceeded=True,
            dimension="seconds",
            limit=float(budget.max_seconds),
            actual=float(refreshed.elapsed_seconds),
        )
    if budget.max_tokens is not None and refreshed.tokens_used > budget.max_tokens:
        return BudgetCheck(
            exceeded=True,
            dimension="tokens",
            limit=float(budget.max_tokens),
            actual=float(refreshed.tokens_used),
        )
    return BudgetCheck(exceeded=False)


def estimate_tokens_from_text(text: str) -> int:
    """Rough char/4 heuristic for streaming text deltas.

    Reasonable enough for budget enforcement; not a substitute for
    real tokenisation when tracking cost / billing.
    """
    if not text:
        return 0
    return max(1, len(text) // 4)


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result >= 0 else None


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result >= 0 else None


__all__ = [
    "Budget",
    "BudgetCheck",
    "BudgetConsumption",
    "check_budget",
    "estimate_tokens_from_text",
]
