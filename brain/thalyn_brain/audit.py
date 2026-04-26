"""Append-only audit log per agent run.

Each run gets a sibling `runs/{run_id}.log` next to its
`runs/{run_id}.db`. The format is NDJSON — one JSON object per line —
with these fields:

    ts        ISO 8601 timestamp (UTC, microsecond precision)
    runId     the run id, repeated on every line for grep-ability
    kind      one of: status | plan_update | action_log | approval |
              approval_required | error
    payload   kind-specific data (the original notification params)

We don't sign or hash-chain in v1 (per F7.6); that's a hardening pass
for the going-public checklist.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from thalyn_brain.orchestration.storage import default_data_dir

Notifier = Callable[[str, Any], Awaitable[None]]

NOTIFICATION_KIND = {
    "run.status": "status",
    "run.plan_update": "plan_update",
    "run.action_log": "action_log",
    "run.approval_required": "approval_required",
}


class AuditLogWriter:
    """Append-only NDJSON writer scoped to a single run."""

    def __init__(self, run_id: str, *, data_dir: Path | None = None) -> None:
        base = data_dir or default_data_dir()
        runs_dir = base / "runs"
        runs_dir.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id
        self.path = runs_dir / f"{run_id}.log"

    def append(self, kind: str, payload: dict[str, Any]) -> None:
        line = json.dumps(
            {
                "ts": _now_iso(),
                "runId": self.run_id,
                "kind": kind,
                "payload": payload,
            },
            separators=(",", ":"),
            ensure_ascii=False,
        )
        # Open per-write so a crash mid-write can't corrupt prior
        # lines; flush on close. v1 scope per F7.6 — no signing.
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def append_approval(self, decision: str, *, edited_plan_present: bool) -> None:
        self.append(
            "approval",
            {"decision": decision, "editedPlanPresent": edited_plan_present},
        )


def wrap_notifier(notify: Notifier, audit: AuditLogWriter | None) -> Notifier:
    """Tee notifications matching audit-relevant methods into the log.

    chat.chunk is intentionally skipped — per-token traffic isn't
    audit material. Everything in NOTIFICATION_KIND lands as a line
    in the audit log alongside the live notification.
    """
    if audit is None:
        return notify

    async def teed(method: str, params: Any) -> None:
        kind = NOTIFICATION_KIND.get(method)
        if kind is not None and isinstance(params, dict):
            audit.append(kind, params)
        await notify(method, params)

    return teed


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")
