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
from thalyn_brain.transport import serve_stdio


def main() -> int:
    data_dir = default_data_dir()
    dispatcher = build_default_dispatcher()
    registry = build_registry()
    runs_store = RunsStore(data_dir=data_dir)
    runner = Runner(registry, runs_store=runs_store, data_dir=data_dir)
    register_chat_methods(dispatcher, registry, runner=runner)
    register_approval_methods(dispatcher, runner)
    register_runs_methods(dispatcher, runs_store, runner=runner)

    async def serve() -> None:
        # Pick up any runs that were in flight when the brain last
        # exited before opening the stdio surface to new traffic.
        await resume_unfinished_runs(runs_store, runner)
        await serve_stdio(dispatcher)

    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
