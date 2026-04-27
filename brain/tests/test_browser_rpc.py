"""JSON-RPC bindings for the browser manager."""

from __future__ import annotations

from typing import Any

from thalyn_brain.browser import BrowserManager
from thalyn_brain.browser_rpc import register_browser_methods
from thalyn_brain.rpc import Dispatcher


class FakeCdpConnection:
    def __init__(self) -> None:
        self.sends: list[dict[str, Any]] = []
        self.closed = False

    async def send(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        session_id: str | None = None,
        timeout: float = 30.0,  # noqa: ASYNC109 — mirrors CdpConnection.send for fakes
    ) -> Any:
        self.sends.append({"method": method, "params": params or {}})
        if method == "Target.getTargets":
            return {"targetInfos": [{"type": "page", "targetId": "tab"}]}
        if method == "Target.attachToTarget":
            return {"sessionId": "S"}
        if method == "Page.enable":
            return {}
        if method == "Page.navigate":
            return {"frameId": "f1", "loaderId": "l1"}
        if method == "Runtime.evaluate":
            return {"result": {"value": "page text"}}
        if method == "Page.captureScreenshot":
            return {"data": "AAAA"}
        return {}

    async def close(self) -> None:
        self.closed = True

    def add_listener(self, _listener: Any) -> None:
        pass


async def _silent_notify(method: str, params: Any) -> None:
    del method, params
    return None


def _build_dispatcher() -> tuple[Dispatcher, BrowserManager]:
    dispatcher = Dispatcher()
    manager = BrowserManager()

    async def connector(_url: str) -> Any:
        return FakeCdpConnection()

    manager.set_connector(connector)
    register_browser_methods(dispatcher, manager)
    return dispatcher, manager


async def test_attach_then_status() -> None:
    dispatcher, _ = _build_dispatcher()

    attach = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "browser.attach",
            "params": {"wsUrl": "ws://127.0.0.1:9222/devtools/browser/x"},
        },
        _silent_notify,
    )
    assert attach is not None
    assert attach["result"]["attached"] is True
    assert attach["result"]["targetId"] == "tab"

    status = await dispatcher.handle(
        {"jsonrpc": "2.0", "id": 2, "method": "browser.status", "params": {}},
        _silent_notify,
    )
    assert status is not None
    assert status["result"]["attached"] is True
    assert status["result"]["session"]["targetId"] == "tab"


async def test_navigate_requires_attachment() -> None:
    dispatcher, _ = _build_dispatcher()
    response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "browser.navigate",
            "params": {"url": "https://example.com"},
        },
        _silent_notify,
    )
    assert response is not None
    assert "error" in response
    assert response["error"]["code"] == -32602  # INVALID_PARAMS


async def test_attach_then_navigate() -> None:
    dispatcher, _ = _build_dispatcher()
    await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "browser.attach",
            "params": {"wsUrl": "ws://127.0.0.1:9222/devtools/browser/x"},
        },
        _silent_notify,
    )
    nav = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "browser.navigate",
            "params": {"url": "https://example.com"},
        },
        _silent_notify,
    )
    assert nav is not None
    assert nav["result"]["frameId"] == "f1"


async def test_screenshot_returns_base64() -> None:
    dispatcher, _ = _build_dispatcher()
    await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "browser.attach",
            "params": {"wsUrl": "ws://127.0.0.1:9222/devtools/browser/x"},
        },
        _silent_notify,
    )
    shot = await dispatcher.handle(
        {"jsonrpc": "2.0", "id": 2, "method": "browser.screenshot", "params": {}},
        _silent_notify,
    )
    assert shot is not None
    assert shot["result"]["pngBase64"] == "AAAA"


async def test_get_text_with_or_without_selector() -> None:
    dispatcher, _ = _build_dispatcher()
    await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "browser.attach",
            "params": {"wsUrl": "ws://127.0.0.1:9222/devtools/browser/x"},
        },
        _silent_notify,
    )
    no_selector = await dispatcher.handle(
        {"jsonrpc": "2.0", "id": 2, "method": "browser.get_text", "params": {}},
        _silent_notify,
    )
    with_selector = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "browser.get_text",
            "params": {"selector": "h1"},
        },
        _silent_notify,
    )
    assert no_selector is not None and no_selector["result"]["text"] == "page text"
    assert with_selector is not None and with_selector["result"]["text"] == "page text"


async def test_attach_validates_ws_url() -> None:
    dispatcher, _ = _build_dispatcher()
    response = await dispatcher.handle(
        {"jsonrpc": "2.0", "id": 1, "method": "browser.attach", "params": {}},
        _silent_notify,
    )
    assert response is not None
    assert response["error"]["code"] == -32602


async def test_detach_when_idle_returns_false() -> None:
    dispatcher, _ = _build_dispatcher()
    response = await dispatcher.handle(
        {"jsonrpc": "2.0", "id": 1, "method": "browser.detach", "params": {}},
        _silent_notify,
    )
    assert response is not None
    assert response["result"]["detached"] is False
