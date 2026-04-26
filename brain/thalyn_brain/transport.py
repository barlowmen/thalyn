"""NDJSON-framed JSON-RPC over an async byte stream.

The walking-skeleton transport is stdio: one request per line on stdin,
one response per line on stdout. The framing is identical to what we'll
use over a Unix domain socket / Windows named pipe later, so the
dispatcher and the framing logic don't need to change at that point —
only the byte-stream factory does.
"""

from __future__ import annotations

import asyncio
import json
import sys

from thalyn_brain.rpc import PARSE_ERROR, Dispatcher, JsonValue


async def serve_stream(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    dispatcher: Dispatcher,
) -> None:
    """Read NDJSON requests from ``reader`` and write responses to ``writer``."""
    while True:
        line = await reader.readline()
        if not line:
            break
        line = line.rstrip(b"\r\n")
        if not line:
            continue

        response = await _process_line(line, dispatcher)
        if response is None:
            continue
        writer.write(_encode(response))
        await writer.drain()


async def _process_line(line: bytes, dispatcher: Dispatcher) -> JsonValue | None:
    try:
        request = json.loads(line)
    except json.JSONDecodeError as exc:
        return {
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": PARSE_ERROR, "message": f"invalid JSON: {exc.msg}"},
        }
    return await dispatcher.handle(request)


def _encode(payload: JsonValue) -> bytes:
    return (json.dumps(payload, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


async def serve_stdio(dispatcher: Dispatcher) -> None:
    """Bind a dispatcher to the process's stdin/stdout.

    Uses blocking reads delegated to a thread executor so we work uniformly
    across pipes, ttys, and re-parented file descriptors. Higher-throughput
    transports (UDS, named pipes) live in their own functions and reuse
    :func:`serve_stream`.
    """
    loop = asyncio.get_running_loop()
    while True:
        line = await loop.run_in_executor(None, sys.stdin.readline)
        if not line:
            return
        stripped = line.rstrip("\r\n")
        if not stripped:
            continue
        response = await _process_line(stripped.encode("utf-8"), dispatcher)
        if response is None:
            continue
        sys.stdout.buffer.write(_encode(response))
        sys.stdout.buffer.flush()
