"""Console entry point: serve JSON-RPC over stdio."""

from __future__ import annotations

import asyncio
import sys

from thalyn_brain.rpc import build_default_dispatcher
from thalyn_brain.transport import serve_stdio


def main() -> int:
    dispatcher = build_default_dispatcher()
    try:
        asyncio.run(serve_stdio(dispatcher))
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
