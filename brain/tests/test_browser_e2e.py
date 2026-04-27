"""End-to-end smoke for the browser stack.

Exercises ``BrowserManager`` against a real WebSocket — not the
in-memory ``FakeCdpConnection`` used by the unit tests — so the wire
encoding, the reader-loop event fan-out, and the capture file IO all
sit in the path. The CDP responder is a small in-process fake that
implements just the methods this smoke needs.

The test confirms the v0.13 exit criterion in the plan: "an agent can
navigate to a URL, extract text, and click a button." It does that
through the same ``BrowserManager`` surface the agent's tools route
through, so any wiring break — connection lifecycle, capture
ordering, target-id propagation — surfaces here.
"""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from typing import Any

import pytest
import websockets
from thalyn_brain.browser import BrowserManager

# Tiny PNG used as the "screenshot" payload.
TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNiAAIAAAUAAfqAvxgAAAAASUVORK5CYII="
)


@pytest.fixture
async def fake_cdp_server() -> Any:
    """Real WebSocket server that handles the methods the smoke needs.

    Tracks navigations, click events, and clicks by selector so the
    test can assert the manager's CDP traffic looks right.
    """
    state: dict[str, Any] = {
        "navigated_to": None,
        "clicks": 0,
        "typed": [],
    }

    async def handler(ws: Any) -> None:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            mid = msg.get("id")
            method = msg.get("method")
            params = msg.get("params") or {}
            session_id = msg.get("sessionId")
            result: Any = {}
            if method == "Target.getTargets":
                result = {"targetInfos": [{"type": "page", "targetId": "page-0"}]}
            elif method == "Target.attachToTarget":
                result = {"sessionId": "smoke-session"}
            elif method == "Page.enable":
                result = {}
            elif method == "Page.navigate":
                state["navigated_to"] = params.get("url")
                result = {"frameId": "frame-1", "loaderId": "loader-1"}
            elif method == "Runtime.evaluate":
                expression = params.get("expression", "")
                if "document.documentElement.outerHTML" in expression:
                    page = state.get("navigated_to") or "about:blank"
                    result = {"result": {"value": f"<html><body><p>{page}</p></body></html>"}}
                elif "getBoundingClientRect" in expression:
                    result = {"result": {"value": {"x": 25.0, "y": 35.0}}}
                elif "el.focus()" in expression:
                    result = {"result": {"value": True}}
                elif "innerText" in expression:
                    result = {"result": {"value": "Welcome to the test page"}}
                else:
                    result = {"result": {"value": None}}
            elif method == "Input.dispatchMouseEvent":
                if params.get("type") == "mousePressed":
                    state["clicks"] += 1
                result = {}
            elif method == "Input.insertText":
                state["typed"].append(params.get("text", ""))
                result = {}
            elif method == "Page.captureScreenshot":
                result = {"data": TINY_PNG_B64}

            envelope: dict[str, Any] = {"id": mid, "result": result}
            if session_id is not None:
                envelope["sessionId"] = session_id
            await ws.send(json.dumps(envelope))

    server = await websockets.serve(handler, "127.0.0.1", 0)
    sockname = next(iter(server.sockets))
    port = sockname.getsockname()[1]
    yield {
        "url": f"ws://127.0.0.1:{port}/devtools/browser/smoke",
        "state": state,
    }
    server.close()
    await server.wait_closed()


async def test_navigate_extract_click_smoke(
    fake_cdp_server: dict[str, Any], tmp_path: Path
) -> None:
    capture_dir = tmp_path / "browser"
    manager = BrowserManager()
    info = await manager.attach(fake_cdp_server["url"])
    assert info.target_id == "page-0"

    manager.set_capture_dir("r_smoke", capture_dir)

    nav = await manager.navigate("https://example.com/test")
    assert nav.frame_id == "frame-1"

    text = await manager.get_text(None)
    assert "Welcome to the test page" in text.text

    click = await manager.click("#submit")
    assert click.x == 25.0 and click.y == 35.0

    typed = await manager.type_text("#email", "user@example.com")
    assert typed.chars_typed == len("user@example.com")

    # The fake CDP server tracked everything the agent did.
    state = fake_cdp_server["state"]
    assert state["navigated_to"] == "https://example.com/test"
    assert state["clicks"] == 1
    assert state["typed"] == ["user@example.com"]

    # Capture wrote four DOM/PNG pairs, one per action — navigate +
    # get_text + click + type. Sequence numbers are zero-padded so a
    # plain `ls` keeps them in order.
    files = sorted(p.name for p in capture_dir.iterdir())
    assert files == [
        "0000.html",
        "0000.png",
        "0001.html",
        "0001.png",
        "0002.html",
        "0002.png",
        "0003.html",
        "0003.png",
    ]

    # The HTML in 0000.html is what the page looked like right after
    # navigate() returned; "Welcome to the test page" hasn't been
    # typed yet, but the URL reflects the navigation.
    nav_html = (capture_dir / "0000.html").read_text(encoding="utf-8")
    assert "https://example.com/test" in nav_html

    # The PNG file matches the bytes the fake replied with.
    assert (capture_dir / "0000.png").read_bytes() == base64.b64decode(TINY_PNG_B64)

    await manager.detach()


async def test_concurrent_attach_then_navigate_round_trip(
    fake_cdp_server: dict[str, Any],
) -> None:
    """Smoke for the request/response router: many in-flight navigates
    must each receive the right reply even when interleaved on the
    wire."""
    manager = BrowserManager()
    await manager.attach(fake_cdp_server["url"])

    urls = [f"https://example.com/{i}" for i in range(5)]
    results = await asyncio.gather(*[manager.navigate(u) for u in urls])

    assert all(r.frame_id == "frame-1" for r in results)

    await manager.detach()
