"""CDP transport — request/response routing and event fan-out."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
import websockets
from thalyn_brain.browser_cdp import (
    CdpClosedError,
    CdpEvent,
    CdpRemoteError,
    connect,
)


@pytest.fixture
async def fake_chromium() -> Any:
    """Stand up a tiny CDP-like WebSocket server.

    The server echoes a fixed set of methods, returns a structured
    error for ``"FailMe.now"``, and emits a single
    ``"Page.frameNavigated"`` event whenever it sees a
    ``"Page.navigate"`` request. We use a real WebSocket so the
    transport-side framing, json round-trip, and reader loop are
    actually exercised.
    """

    async def handler(ws: Any) -> None:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            mid = msg.get("id")
            method = msg.get("method")
            if method == "FailMe.now":
                await ws.send(
                    json.dumps(
                        {
                            "id": mid,
                            "error": {"code": -32000, "message": "boom"},
                        }
                    )
                )
                continue
            if method == "BlockMe.forever":
                # Never reply — used to exercise the close-while-pending path.
                continue
            # Default: echo the params back as the result.
            await ws.send(
                json.dumps(
                    {
                        "id": mid,
                        "result": {"echoed": msg.get("params"), "method": method},
                    }
                )
            )
            if method == "Page.navigate":
                await ws.send(
                    json.dumps(
                        {
                            "method": "Page.frameNavigated",
                            "params": {"frame": {"url": "about:blank"}},
                        }
                    )
                )

    server = await websockets.serve(handler, "127.0.0.1", 0)
    sockname = next(iter(server.sockets))
    port = sockname.getsockname()[1]
    yield f"ws://127.0.0.1:{port}/devtools/browser/test"
    server.close()
    await server.wait_closed()


async def test_request_response_round_trip(fake_chromium: str) -> None:
    async with connect(fake_chromium) as cdp:
        result = await cdp.send("Browser.getVersion", {"hello": "world"})
        assert result == {"echoed": {"hello": "world"}, "method": "Browser.getVersion"}


async def test_remote_error_is_typed(fake_chromium: str) -> None:
    async with connect(fake_chromium) as cdp:
        with pytest.raises(CdpRemoteError) as excinfo:
            await cdp.send("FailMe.now", {})
        assert excinfo.value.code == -32000
        assert "boom" in str(excinfo.value)


async def test_listener_receives_events(fake_chromium: str) -> None:
    async with connect(fake_chromium) as cdp:
        events: list[CdpEvent] = []
        cdp.add_listener(events.append)
        await cdp.send("Page.navigate", {"url": "https://example.com"})
        # Give the reader loop a moment to surface the event.
        for _ in range(20):
            if events:
                break
            await asyncio.sleep(0.01)
        assert events, "expected at least one event"
        assert events[0].method == "Page.frameNavigated"


async def test_pending_request_fails_when_connection_closes(fake_chromium: str) -> None:
    async with connect(fake_chromium) as cdp:
        # The fake server never replies to "BlockMe.forever"; closing
        # the underlying ws while the request is pending must surface
        # CdpClosedError rather than hang.
        send_task = asyncio.create_task(cdp.send("BlockMe.forever", {}, timeout=2.0))
        await asyncio.sleep(0.05)
        await cdp._ws.close()
        with pytest.raises(CdpClosedError):
            await send_task


async def test_unknown_endpoint_raises_cleanly() -> None:
    # No WS server listening — connect should surface a typed error.
    from thalyn_brain.browser_cdp import CdpError

    with pytest.raises(CdpError):
        async with connect("ws://127.0.0.1:1/devtools/browser/none", open_timeout=0.5):
            pass
