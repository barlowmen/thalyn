"""Console entry point: serve JSON-RPC over stdio."""

from __future__ import annotations

import asyncio
import sys

from thalyn_brain.chat import register_chat_methods
from thalyn_brain.provider import build_registry
from thalyn_brain.rpc import build_default_dispatcher
from thalyn_brain.transport import serve_stdio


def main() -> int:
    dispatcher = build_default_dispatcher()
    registry = build_registry()
    register_chat_methods(dispatcher, registry)
    try:
        asyncio.run(serve_stdio(dispatcher))
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
