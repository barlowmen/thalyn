"""Console entry point: serve JSON-RPC over stdio."""

from __future__ import annotations

import asyncio
import sys

from thalyn_brain.approval_rpc import register_approval_methods
from thalyn_brain.chat import register_chat_methods
from thalyn_brain.orchestration import Runner
from thalyn_brain.orchestration.resume import resume_unfinished_runs
from thalyn_brain.orchestration.storage import default_data_dir
from thalyn_brain.provider import build_registry
from thalyn_brain.rpc import build_default_dispatcher
from thalyn_brain.runs import RunsStore
from thalyn_brain.runs_rpc import register_runs_methods
from thalyn_brain.schedules import (
    Schedule,
    SchedulerLoop,
    SchedulesStore,
)
from thalyn_brain.schedules_rpc import register_schedule_methods
from thalyn_brain.transport import serve_stdio


def main() -> int:
    data_dir = default_data_dir()
    dispatcher = build_default_dispatcher()
    registry = build_registry()
    runs_store = RunsStore(data_dir=data_dir)
    schedules_store = SchedulesStore(data_dir=data_dir)
    runner = Runner(registry, runs_store=runs_store, data_dir=data_dir)
    register_chat_methods(dispatcher, registry, runner=runner)
    register_approval_methods(dispatcher, runner)
    register_runs_methods(dispatcher, runs_store, runner=runner)
    register_schedule_methods(dispatcher, schedules_store, registry)

    async def dispatch_schedule(schedule: Schedule) -> str | None:
        """Fire one schedule into the runner.

        Notifications fall through into a sink — the renderer doesn't
        observe scheduled runs the same way it does interactive
        ones; the runs index is the durable record.
        """
        run_template = schedule.run_template
        provider_id = run_template.get("providerId", "anthropic")
        prompt = run_template.get("prompt", "")
        if not isinstance(prompt, str) or not prompt:
            return None
        if not isinstance(provider_id, str):
            return None

        async def sink(_method: str, _params: object) -> None:
            return None

        try:
            result = await runner.run(
                session_id=f"schedule-{schedule.schedule_id}",
                provider_id=provider_id,
                prompt=prompt,
                notify=sink,
            )
        except Exception:
            return None
        return result.run_id

    scheduler = SchedulerLoop(schedules_store, dispatch_schedule)

    async def serve() -> None:
        # Pick up any runs that were in flight when the brain last
        # exited before opening the stdio surface to new traffic.
        await resume_unfinished_runs(runs_store, runner)
        scheduler.start()
        try:
            await serve_stdio(dispatcher)
        finally:
            await scheduler.stop()

    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
