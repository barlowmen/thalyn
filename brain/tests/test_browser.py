"""BrowserManager — attach lifecycle and primitive tool calls."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from thalyn_brain.browser import (
    BrowserAlreadyAttachedError,
    BrowserError,
    BrowserManager,
    BrowserNotAttachedError,
)


class FakeCdpConnection:
    """Stand-in for ``CdpConnection`` that records sends and returns
    canned responses. Tracks the active session-id prefix so the
    manager's ``Target.attachToTarget`` flow is exercised end-to-end."""

    def __init__(self) -> None:
        self.sends: list[dict[str, Any]] = []
        self.closed = False
        self._target_id = "target_1"
        self._session_id = "sess_1"
        self._screenshot_data = (
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAA"
            "AC0lEQVR4nGNiAAIAAAUAAfqAvxgAAAAASUVORK5CYII="
        )

    async def send(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        session_id: str | None = None,
        timeout: float = 30.0,  # noqa: ASYNC109 — mirrors CdpConnection.send for fakes
    ) -> Any:
        self.sends.append({"method": method, "params": params or {}, "sessionId": session_id})
        if method == "Target.getTargets":
            return {
                "targetInfos": [{"type": "page", "targetId": self._target_id, "url": "about:blank"}]
            }
        if method == "Target.attachToTarget":
            return {"sessionId": self._session_id}
        if method == "Page.enable":
            return {}
        if method == "Page.navigate":
            return {"frameId": "frame-1", "loaderId": "loader-1"}
        if method == "Runtime.evaluate":
            expression = (params or {}).get("expression", "")
            if "getBoundingClientRect" in expression:
                return {"result": {"value": {"x": 50.0, "y": 75.0}}}
            if "el.focus()" in expression:
                return {"result": {"value": True}}
            if "innerText" in expression:
                return {"result": {"value": "hello world"}}
            return {"result": {"value": None}}
        if method == "Input.dispatchMouseEvent":
            return {}
        if method == "Input.insertText":
            return {}
        if method == "Page.captureScreenshot":
            return {"data": self._screenshot_data}
        return {}

    async def close(self) -> None:
        self.closed = True

    def add_listener(self, _listener: Any) -> None:
        pass

    def remove_listener(self, _listener: Any) -> None:
        pass


async def _make_attached_manager() -> tuple[BrowserManager, FakeCdpConnection]:
    fake = FakeCdpConnection()

    async def connector(_ws_url: str) -> Any:
        return fake

    manager = BrowserManager()
    manager.set_connector(connector)
    await manager.attach("ws://127.0.0.1:9222/devtools/browser/x")
    return manager, fake


async def test_attach_picks_active_target_and_session() -> None:
    manager, fake = await _make_attached_manager()
    info = manager.attached_info()
    assert info is not None
    assert info.target_id == "target_1"
    methods = [s["method"] for s in fake.sends]
    assert methods[0] == "Target.getTargets"
    assert methods[1] == "Target.attachToTarget"
    assert methods[2] == "Page.enable"


async def test_double_attach_raises() -> None:
    manager, _ = await _make_attached_manager()

    async def connector(_ws_url: str) -> Any:
        return FakeCdpConnection()

    manager.set_connector(connector)
    with pytest.raises(BrowserAlreadyAttachedError):
        await manager.attach("ws://127.0.0.1:9222/devtools/browser/y")


async def test_detach_closes_connection() -> None:
    manager, fake = await _make_attached_manager()
    detached = await manager.detach()
    assert detached is True
    assert fake.closed is True
    assert manager.attached_info() is None
    assert (await manager.detach()) is False


async def test_navigate_routes_through_session() -> None:
    manager, fake = await _make_attached_manager()
    result = await manager.navigate("https://example.com")
    assert result.frame_id == "frame-1"
    last = fake.sends[-1]
    assert last["method"] == "Page.navigate"
    assert last["params"] == {"url": "https://example.com"}
    assert last["sessionId"] == "sess_1"


async def test_get_text_full_page() -> None:
    manager, _ = await _make_attached_manager()
    result = await manager.get_text(None)
    assert result.text == "hello world"


async def test_click_uses_element_center() -> None:
    manager, fake = await _make_attached_manager()
    result = await manager.click("#submit")
    assert result.x == 50.0 and result.y == 75.0
    mouse_calls = [s for s in fake.sends if s["method"] == "Input.dispatchMouseEvent"]
    assert len(mouse_calls) == 2  # press + release
    assert mouse_calls[0]["params"]["x"] == 50.0


async def test_type_focuses_then_inserts() -> None:
    manager, fake = await _make_attached_manager()
    result = await manager.type_text("#email", "user@example.com")
    assert result.chars_typed == len("user@example.com")
    insert_calls = [s for s in fake.sends if s["method"] == "Input.insertText"]
    assert insert_calls and insert_calls[0]["params"]["text"] == "user@example.com"


async def test_screenshot_returns_base64_payload() -> None:
    manager, _ = await _make_attached_manager()
    result = await manager.screenshot()
    assert result.png_base64.startswith("iVBORw0KGgo")  # PNG header in base64


async def test_operations_require_attachment() -> None:
    manager = BrowserManager()
    with pytest.raises(BrowserNotAttachedError):
        await manager.navigate("https://example.com")
    with pytest.raises(BrowserNotAttachedError):
        await manager.click("#x")


async def test_attach_failure_cleans_up() -> None:
    """If picking the target fails, attach must close the connection."""

    class NoTargetsConn(FakeCdpConnection):
        async def send(
            self,
            method: str,
            params: dict[str, Any] | None = None,
            *,
            session_id: str | None = None,
            timeout: float = 30.0,  # noqa: ASYNC109 — mirrors CdpConnection.send for fakes
        ) -> Any:
            if method == "Target.getTargets":
                return {"targetInfos": []}
            return await super().send(method, params, session_id=session_id, timeout=timeout)

    fake = NoTargetsConn()

    async def connector(_ws_url: str) -> Any:
        return fake

    manager = BrowserManager()
    manager.set_connector(connector)
    with pytest.raises(BrowserError):
        await manager.attach("ws://127.0.0.1:9222/devtools/browser/x")
    assert fake.closed is True
    assert manager.attached_info() is None


async def test_attach_is_serialised() -> None:
    """Concurrent attach calls don't both succeed."""

    fake_count = 0

    async def slow_connector(_ws_url: str) -> Any:
        nonlocal fake_count
        await asyncio.sleep(0.01)
        fake_count += 1
        return FakeCdpConnection()

    manager = BrowserManager()
    manager.set_connector(slow_connector)
    results = await asyncio.gather(
        manager.attach("ws://127.0.0.1:9222/devtools/browser/x"),
        manager.attach("ws://127.0.0.1:9222/devtools/browser/x"),
        return_exceptions=True,
    )
    successes = [r for r in results if not isinstance(r, BaseException)]
    failures = [r for r in results if isinstance(r, BrowserAlreadyAttachedError)]
    assert len(successes) == 1
    assert len(failures) == 1
