"""On-startup resume — pick up runs that were in flight when the
brain last exited.

Scans the runs index for any header in a non-terminal state, attempts
to resume the run from its last checkpoint, and marks the header
``errored`` when the resume itself fails. Resuming uses a no-op
notifier because no client is connected at boot time; the renderer
sees the resumed state next time it polls the runs index.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from thalyn_brain.orchestration.runner import Runner
from thalyn_brain.orchestration.state import RunStatus
from thalyn_brain.runs import RunsStore, RunUpdate

logger = logging.getLogger(__name__)


async def _silent_notifier(_method: str, _params: Any) -> None:
    return None


async def resume_unfinished_runs(
    runs_store: RunsStore,
    runner: Runner,
) -> list[str]:
    """Resume every unfinished run; returns the run ids that were touched.

    Each entry returned is one of: a successfully-resumed run id,
    or a run id whose header was marked ``errored`` because the
    resume itself failed (e.g. a missing API key, a corrupted
    checkpoint, an unknown provider).
    """
    headers = await runs_store.list_unfinished()
    if not headers:
        return []

    touched: list[str] = []
    for header in headers:
        try:
            result = await runner.resume(
                run_id=header.run_id,
                provider_id=header.provider_id,
                notify=_silent_notifier,
            )
            if result is None:
                # No checkpoint to pick up — mark the run errored
                # because we can't reconstruct enough to continue.
                await runs_store.update(
                    header.run_id,
                    RunUpdate(
                        status=RunStatus.ERRORED.value,
                        completed_at_ms=int(time.time() * 1000),
                        final_response="resume failed: no checkpoint to pick up",
                    ),
                )
            touched.append(header.run_id)
        except Exception as exc:
            logger.exception("resume failed for run %s", header.run_id)
            await runs_store.update(
                header.run_id,
                RunUpdate(
                    status=RunStatus.ERRORED.value,
                    completed_at_ms=int(time.time() * 1000),
                    final_response=f"resume failed: {exc}",
                ),
            )
            touched.append(header.run_id)

    return touched
