"""Per-step DOM + screenshot capture for action-log replay."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import pytest
from thalyn_brain.browser import BrowserManager

# A 1x1 transparent PNG, base64-encoded.
TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNiAAIAAAUAAfqAvxgAAAAASUVORK5CYII="
)


class FakeCdpConnection:
    def __init__(self) -> None:
        self.sends: list[dict[str, Any]] = []
        self.closed = False
        self._dom_html = "<html><body><h1>hello</h1></body></html>"

    async def send(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        session_id: str | None = None,
        timeout: float = 30.0,  # noqa: ASYNC109 — mirrors CdpConnection.send
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
            expression = (params or {}).get("expression", "")
            if "document.documentElement.outerHTML" in expression:
                return {"result": {"value": self._dom_html}}
            if "getBoundingClientRect" in expression:
                return {"result": {"value": {"x": 10.0, "y": 20.0}}}
            if "el.focus()" in expression:
                return {"result": {"value": True}}
            if "innerText" in expression:
                return {"result": {"value": "page text"}}
            return {"result": {"value": None}}
        if method == "Input.dispatchMouseEvent":
            return {}
        if method == "Input.insertText":
            return {}
        if method == "Page.captureScreenshot":
            return {"data": TINY_PNG_B64}
        return {}

    async def close(self) -> None:
        self.closed = True


async def _attach(manager: BrowserManager) -> None:
    fake = FakeCdpConnection()

    async def connector(_url: str) -> Any:
        return fake

    manager.set_connector(connector)
    await manager.attach("ws://127.0.0.1:9222/devtools/browser/x")


async def test_capture_writes_dom_and_screenshot(tmp_path: Path) -> None:
    manager = BrowserManager()
    await _attach(manager)
    manager.set_capture_dir("r_1", tmp_path)

    result = await manager.capture()
    assert result is not None
    assert result.step_seq == 0
    assert Path(result.dom_path).exists()
    assert Path(result.screenshot_path).exists()

    # DOM file is the rendered HTML.
    assert "<h1>hello</h1>" in Path(result.dom_path).read_text(encoding="utf-8")

    # PNG file decodes back to the same bytes the fake returned.
    assert Path(result.screenshot_path).read_bytes() == base64.b64decode(TINY_PNG_B64)


async def test_capture_increments_step_counter(tmp_path: Path) -> None:
    manager = BrowserManager()
    await _attach(manager)
    manager.set_capture_dir("r_1", tmp_path)

    first = await manager.capture()
    second = await manager.capture()
    assert first is not None and second is not None
    assert first.step_seq == 0
    assert second.step_seq == 1
    assert Path(first.dom_path).name == "0000.html"
    assert Path(second.dom_path).name == "0001.html"


async def test_capture_no_op_without_capture_dir(tmp_path: Path) -> None:
    manager = BrowserManager()
    await _attach(manager)
    # No set_capture_dir call.
    assert await manager.capture() is None


async def test_tools_auto_capture_after_action(tmp_path: Path) -> None:
    manager = BrowserManager()
    await _attach(manager)
    manager.set_capture_dir("r_1", tmp_path)

    await manager.navigate("https://example.com")
    await manager.click("#submit")
    await manager.type_text("#email", "user@example.com")

    files = sorted(p.name for p in tmp_path.iterdir())
    # Three actions, two files each (html + png).
    assert files == [
        "0000.html",
        "0000.png",
        "0001.html",
        "0001.png",
        "0002.html",
        "0002.png",
    ]


async def test_capture_dir_resets_step_counter_on_new_run(tmp_path: Path) -> None:
    manager = BrowserManager()
    await _attach(manager)
    run_one_dir = tmp_path / "r_1"
    run_two_dir = tmp_path / "r_2"

    manager.set_capture_dir("r_1", run_one_dir)
    a = await manager.capture()
    assert a is not None and a.step_seq == 0

    manager.set_capture_dir("r_2", run_two_dir)
    b = await manager.capture()
    assert b is not None
    assert b.step_seq == 0  # reset
    assert Path(b.dom_path).parent == run_two_dir


async def test_detach_clears_capture_dir(tmp_path: Path) -> None:
    manager = BrowserManager()
    await _attach(manager)
    manager.set_capture_dir("r_1", tmp_path)

    await manager.detach()
    run, path, seq = manager.capture_state()
    assert run is None
    assert path is None
    assert seq == 0


async def test_capture_failure_does_not_break_tool_call(tmp_path: Path) -> None:
    """Best-effort capture: an IO failure during auto-capture
    must not propagate to the agent."""

    class CdpConnWithFail(FakeCdpConnection):
        async def send(
            self,
            method: str,
            params: dict[str, Any] | None = None,
            *,
            session_id: str | None = None,
            timeout: float = 30.0,  # noqa: ASYNC109
        ) -> Any:
            if method == "Page.captureScreenshot":
                raise RuntimeError("disk full")
            return await super().send(method, params, session_id=session_id, timeout=timeout)

    fake = CdpConnWithFail()

    async def connector(_url: str) -> Any:
        return fake

    manager = BrowserManager()
    manager.set_connector(connector)
    await manager.attach("ws://127.0.0.1:9222/devtools/browser/x")
    manager.set_capture_dir("r_1", tmp_path)

    # Tool call must succeed even though capture fails.
    result = await manager.navigate("https://example.com")
    assert result.frame_id == "f1"


@pytest.mark.parametrize("base", ["string", "pathlib"])
async def test_set_capture_dir_accepts_str_or_path(tmp_path: Path, base: str) -> None:
    manager = BrowserManager()
    await _attach(manager)
    arg: str | Path = str(tmp_path) if base == "string" else tmp_path
    manager.set_capture_dir("r_1", arg)
    _, path, _ = manager.capture_state()
    assert path == tmp_path
