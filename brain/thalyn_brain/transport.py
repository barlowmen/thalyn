"""NDJSON-framed JSON-RPC over an async byte stream.

The walking-skeleton transport is stdio: one request per line on stdin,
one response per line on stdout. The framing is identical to what we'll
use over a Unix domain socket / Windows named pipe later, so the
dispatcher and the framing logic don't need to change at that point —
only the byte-stream factory does.

A ``Notifier`` is threaded through to the dispatcher so request
handlers can emit JSON-RPC notifications mid-flight (used for chat
chunks, run lifecycle events, drift findings).
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Awaitable, Callable

from thalyn_brain.rpc import PARSE_ERROR, Dispatcher, JsonValue, Notifier

WriteLine = Callable[[bytes], Awaitable[None]]


async def serve_stream(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    dispatcher: Dispatcher,
) -> None:
    """Read NDJSON requests from ``reader`` and write responses to ``writer``."""

    write_lock = asyncio.Lock()

    async def write_line(line: bytes) -> None:
        async with write_lock:
            writer.write(line)
            await writer.drain()

    notify = _make_notifier(write_line)

    while True:
        line = await reader.readline()
        if not line:
            break
        line = line.rstrip(b"\r\n")
        if not line:
            continue

        response = await _process_line(line, dispatcher, notify)
        if response is None:
            continue
        await write_line(_encode(response))


async def _process_line(
    line: bytes,
    dispatcher: Dispatcher,
    notify: Notifier,
) -> JsonValue | None:
    try:
        request = json.loads(line)
    except json.JSONDecodeError as exc:
        return {
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": PARSE_ERROR, "message": f"invalid JSON: {exc.msg}"},
        }
    return await dispatcher.handle(request, notify)


def _encode(payload: JsonValue) -> bytes:
    return (json.dumps(payload, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def _make_notifier(write_line: WriteLine) -> Notifier:
    async def notify(method: str, params: JsonValue) -> None:
        payload = {"jsonrpc": "2.0", "method": method, "params": params}
        await write_line(_encode(payload))

    return notify


async def serve_stdio(dispatcher: Dispatcher) -> None:
    """Bind a dispatcher to the process's stdin/stdout.

    Uses blocking reads delegated to a thread executor so we work
    uniformly across pipes, ttys, and re-parented file descriptors.
    Writes are serialised through a single lock so notifications
    emitted from a streaming handler don't interleave with the
    request's own response line.
    """
    loop = asyncio.get_running_loop()
    write_lock = asyncio.Lock()

    async def write_line(line: bytes) -> None:
        async with write_lock:
            sys.stdout.buffer.write(line)
            sys.stdout.buffer.flush()

    notify = _make_notifier(write_line)

    while True:
        line = await loop.run_in_executor(None, sys.stdin.readline)
        if not line:
            return
        stripped = line.rstrip("\r\n")
        if not stripped:
            continue
        response = await _process_line(stripped.encode("utf-8"), dispatcher, notify)
        if response is None:
            continue
        await write_line(_encode(response))
