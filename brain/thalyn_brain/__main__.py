"""Console entry point: serve JSON-RPC over stdio."""

from __future__ import annotations

import asyncio
import sys

from thalyn_brain.chat import register_chat_methods
from thalyn_brain.orchestration import Runner
from thalyn_brain.orchestration.storage import default_data_dir
from thalyn_brain.provider import build_registry
from thalyn_brain.rpc import build_default_dispatcher
from thalyn_brain.transport import serve_stdio


def main() -> int:
    dispatcher = build_default_dispatcher()
    registry = build_registry()
    runner = Runner(registry, data_dir=default_data_dir())
    register_chat_methods(dispatcher, registry, runner=runner)
    try:
        asyncio.run(serve_stdio(dispatcher))
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
