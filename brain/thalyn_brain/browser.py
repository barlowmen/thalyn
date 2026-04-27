"""High-level browser session for the brain sidecar.

Wraps a :class:`CdpConnection` with the half-dozen primitives the
agent's browser tool needs — navigate, get-text, click, type,
screenshot — plus the attach / detach lifecycle the renderer drives
when the Rust core spawns the headed Chromium sidecar.

The Rust core owns the actual Chromium child (per ADR-0010 + the
v0.13 commit-1 implementation refinement). The brain attaches to the
WS URL the core hands it, picks the active page target via
``Target.getTargets``, and reuses that target for the rest of the
session. v0.13 ships single-page semantics; multi-target navigation
(``window.open`` flows, etc.) are scoped for a later refinement.
"""

from __future__ import annotations

import asyncio
import base64
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from thalyn_brain.browser_cdp import (
    CdpConnection,
    CdpError,
)
from thalyn_brain.browser_cdp import (
    connect as connect_cdp,
)

JsonValue = Any


class BrowserError(RuntimeError):
    """Generic browser-tool error."""


class BrowserNotAttachedError(BrowserError):
    """Operation requires an attached session, but none is open."""


class BrowserAlreadyAttachedError(BrowserError):
    """Caller attached a session while one was already open."""


@dataclass(frozen=True)
class AttachInfo:
    """Wire-friendly snapshot of an attached session."""

    ws_url: str
    target_id: str
    attached_at_ms: int

    def to_wire(self) -> dict[str, JsonValue]:
        return {
            "wsUrl": self.ws_url,
            "targetId": self.target_id,
            "attachedAtMs": self.attached_at_ms,
        }


@dataclass(frozen=True)
class NavigateResult:
    target_id: str
    frame_id: str | None
    loader_id: str | None

    def to_wire(self) -> dict[str, JsonValue]:
        return {
            "targetId": self.target_id,
            "frameId": self.frame_id,
            "loaderId": self.loader_id,
        }


@dataclass(frozen=True)
class GetTextResult:
    text: str
    target_id: str

    def to_wire(self) -> dict[str, JsonValue]:
        return {"text": self.text, "targetId": self.target_id}


@dataclass(frozen=True)
class ClickResult:
    target_id: str
    x: float
    y: float

    def to_wire(self) -> dict[str, JsonValue]:
        return {"targetId": self.target_id, "x": self.x, "y": self.y}


@dataclass(frozen=True)
class TypeResult:
    target_id: str
    chars_typed: int

    def to_wire(self) -> dict[str, JsonValue]:
        return {"targetId": self.target_id, "charsTyped": self.chars_typed}


@dataclass(frozen=True)
class ScreenshotResult:
    """Base64-encoded PNG screenshot."""

    target_id: str
    png_base64: str

    def to_wire(self) -> dict[str, JsonValue]:
        return {"targetId": self.target_id, "pngBase64": self.png_base64}


@dataclass(frozen=True)
class CaptureResult:
    """A point-in-time DOM + screenshot saved to disk for action-log replay."""

    target_id: str
    step_seq: int
    dom_path: str
    screenshot_path: str
    captured_at_ms: int

    def to_wire(self) -> dict[str, JsonValue]:
        return {
            "targetId": self.target_id,
            "stepSeq": self.step_seq,
            "domPath": self.dom_path,
            "screenshotPath": self.screenshot_path,
            "capturedAtMs": self.captured_at_ms,
        }


class _Session:
    """Live browser session — connection + selected target + cdp ops."""

    def __init__(
        self,
        connection: CdpConnection,
        target_id: str,
        session_id: str,
        ws_url: str,
        attached_at_ms: int,
    ) -> None:
        self.connection = connection
        self.target_id = target_id
        self.session_id = session_id
        self.ws_url = ws_url
        self.attached_at_ms = attached_at_ms

    async def navigate(self, url: str) -> NavigateResult:
        result = await self.connection.send(
            "Page.navigate",
            {"url": url},
            session_id=self.session_id,
        )
        return NavigateResult(
            target_id=self.target_id,
            frame_id=_optional_str(result.get("frameId")),
            loader_id=_optional_str(result.get("loaderId")),
        )

    async def get_text(self, selector: str | None) -> GetTextResult:
        if selector is None:
            expression = "document.body && document.body.innerText"
        else:
            # JSON-encode the selector so quotes / specials don't
            # break the JS expression.
            import json as _json

            quoted = _json.dumps(selector)
            expression = (
                "(() => {"
                f"const el = document.querySelector({quoted});"
                "return el ? (el.innerText ?? el.textContent ?? '') : null;"
                "})()"
            )
        result = await self.connection.send(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": False,
            },
            session_id=self.session_id,
        )
        value = result.get("result", {}).get("value")
        text = value if isinstance(value, str) else ""
        return GetTextResult(text=text, target_id=self.target_id)

    async def click(self, selector: str) -> ClickResult:
        x, y = await self._element_center(selector)
        for event_type in ("mousePressed", "mouseReleased"):
            await self.connection.send(
                "Input.dispatchMouseEvent",
                {
                    "type": event_type,
                    "x": x,
                    "y": y,
                    "button": "left",
                    "clickCount": 1,
                },
                session_id=self.session_id,
            )
        return ClickResult(target_id=self.target_id, x=x, y=y)

    async def type_text(self, selector: str, text: str) -> TypeResult:
        await self._focus(selector)
        await self.connection.send(
            "Input.insertText",
            {"text": text},
            session_id=self.session_id,
        )
        return TypeResult(target_id=self.target_id, chars_typed=len(text))

    async def screenshot(self) -> ScreenshotResult:
        result = await self.connection.send(
            "Page.captureScreenshot",
            {"format": "png", "captureBeyondViewport": False},
            session_id=self.session_id,
        )
        data = result.get("data")
        if not isinstance(data, str):
            raise BrowserError("captureScreenshot returned no data")
        return ScreenshotResult(target_id=self.target_id, png_base64=data)

    async def dom_html(self) -> str:
        """Return the rendered HTML of the active page."""
        result = await self.connection.send(
            "Runtime.evaluate",
            {
                "expression": "document.documentElement.outerHTML",
                "returnByValue": True,
                "awaitPromise": False,
            },
            session_id=self.session_id,
        )
        value = result.get("result", {}).get("value")
        return value if isinstance(value, str) else ""

    async def _element_center(self, selector: str) -> tuple[float, float]:
        import json as _json

        quoted = _json.dumps(selector)
        expression = (
            "(() => {"
            f"const el = document.querySelector({quoted});"
            "if (!el) return null;"
            "const r = el.getBoundingClientRect();"
            "return { x: r.left + r.width / 2, y: r.top + r.height / 2 };"
            "})()"
        )
        result = await self.connection.send(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": False,
            },
            session_id=self.session_id,
        )
        value = result.get("result", {}).get("value")
        if not isinstance(value, dict):
            raise BrowserError(f"selector {selector!r} did not match an element")
        x = value.get("x")
        y = value.get("y")
        if not (isinstance(x, int | float) and isinstance(y, int | float)):
            raise BrowserError(f"could not compute click point for {selector!r}")
        return float(x), float(y)

    async def _focus(self, selector: str) -> None:
        import json as _json

        quoted = _json.dumps(selector)
        expression = (
            "(() => {"
            f"const el = document.querySelector({quoted});"
            "if (!el) return false;"
            "el.focus();"
            "return true;"
            "})()"
        )
        result = await self.connection.send(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": False,
            },
            session_id=self.session_id,
        )
        value = result.get("result", {}).get("value")
        if value is not True:
            raise BrowserError(f"could not focus selector {selector!r}")


class BrowserManager:
    """Single-session browser manager bound to one Chromium WS URL.

    Lifecycle:

    1. Renderer spawns Chromium via the Rust core, gets a WS URL.
    2. Renderer (or core) calls ``manager.attach(ws_url)``.
    3. Manager opens a CDP connection, picks the active page target
       via ``Target.getTargets``, opens an ``attachToTarget`` session
       with ``flatten=True`` so future messages can be routed by
       ``sessionId``.
    4. Agent tools read the current session via ``manager.session()``.
    5. Renderer (or core) calls ``manager.detach()`` to close.

    Per-step capture:

    When the runner starts a browser-using agent it calls
    ``set_capture_dir(run_id, base_dir)``. The manager then snapshots
    the rendered DOM and a PNG screenshot after every successful
    navigate / click / type / get_text — written to
    ``<base_dir>/<step_seq>.{html,png}`` so the action log can
    replay an agent's browser run after the fact.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._session: _Session | None = None
        # Connector is injectable so tests can substitute a fake
        # without touching real websockets.
        self._connector = _default_connector
        # Per-step capture state — set by the runner via
        # ``set_capture_dir`` and torn down on detach.
        self._capture_dir: Path | None = None
        self._capture_run_id: str | None = None
        self._step_seq = 0

    def set_connector(self, connector: _Connector) -> None:
        """Replace the connection factory; intended for tests."""
        self._connector = connector

    def is_attached(self) -> bool:
        return self._session is not None

    def set_capture_dir(self, run_id: str, base_dir: Path | str) -> None:
        """Enable per-step capture for the active session.

        Subsequent tool calls will write a DOM dump + PNG screenshot
        to ``<base_dir>/<seq>.{html,png}``. Calling again with a
        different ``run_id`` resets the step counter.
        """
        path = Path(base_dir)
        path.mkdir(parents=True, exist_ok=True)
        if run_id != self._capture_run_id:
            self._step_seq = 0
        self._capture_dir = path
        self._capture_run_id = run_id

    def clear_capture_dir(self) -> None:
        self._capture_dir = None
        self._capture_run_id = None
        self._step_seq = 0

    def capture_state(self) -> tuple[str | None, Path | None, int]:
        """Snapshot of the capture configuration; for tests + RPC."""
        return self._capture_run_id, self._capture_dir, self._step_seq

    async def attach(self, ws_url: str) -> AttachInfo:
        async with self._lock:
            if self._session is not None:
                raise BrowserAlreadyAttachedError("a browser session is already attached")
            try:
                conn = await self._connector(ws_url)
            except CdpError as exc:
                raise BrowserError(str(exc)) from exc
            try:
                target_id = await _pick_active_target(conn)
                session_id = await _attach_to_target(conn, target_id)
                # Make sure the page domain is enabled — most CDP
                # commands need this and Chromium quietly errors
                # otherwise.
                await conn.send("Page.enable", {}, session_id=session_id)
            except Exception:
                with suppress(Exception):
                    await conn.close()
                raise
            now_ms = int(time.time() * 1000)
            self._session = _Session(
                connection=conn,
                target_id=target_id,
                session_id=session_id,
                ws_url=ws_url,
                attached_at_ms=now_ms,
            )
            return AttachInfo(ws_url=ws_url, target_id=target_id, attached_at_ms=now_ms)

    async def detach(self) -> bool:
        async with self._lock:
            session = self._session
            if session is None:
                return False
            self._session = None
            self.clear_capture_dir()
            with suppress(Exception):
                await session.connection.close()
            return True

    def session(self) -> _Session:
        session = self._session
        if session is None:
            raise BrowserNotAttachedError("no browser session attached")
        return session

    def attached_info(self) -> AttachInfo | None:
        session = self._session
        if session is None:
            return None
        return AttachInfo(
            ws_url=session.ws_url,
            target_id=session.target_id,
            attached_at_ms=session.attached_at_ms,
        )

    # ----- Tool-facing convenience wrappers -----
    #
    # These thin wrappers exist so callers (RPC handlers, tool
    # entries) don't have to navigate through ``session()`` for the
    # common path. They also enforce the "must be attached" precondition
    # in one place. Each successful action triggers a capture if a
    # capture dir is set, so the action log can reference the on-disk
    # snapshot without the caller having to manage step counters.

    async def navigate(self, url: str) -> NavigateResult:
        result = await self.session().navigate(url)
        await self._maybe_capture()
        return result

    async def get_text(self, selector: str | None = None) -> GetTextResult:
        result = await self.session().get_text(selector)
        await self._maybe_capture()
        return result

    async def click(self, selector: str) -> ClickResult:
        result = await self.session().click(selector)
        await self._maybe_capture()
        return result

    async def type_text(self, selector: str, text: str) -> TypeResult:
        result = await self.session().type_text(selector, text)
        await self._maybe_capture()
        return result

    async def _maybe_capture(self) -> None:
        if self._capture_dir is None:
            return
        try:
            await self.capture()
        except Exception:
            # Capture must never break the agent — best-effort only.
            return

    async def screenshot(self) -> ScreenshotResult:
        return await self.session().screenshot()

    async def capture(self) -> CaptureResult | None:
        """Capture DOM + PNG to the configured capture dir.

        No-ops with ``None`` if no capture dir has been set — the
        common path for ad-hoc tool invocations outside a run.
        """
        if self._capture_dir is None or self._capture_run_id is None:
            return None
        sess = self.session()
        seq = self._step_seq
        self._step_seq += 1
        # Capture in parallel; both are read-only CDP calls.
        dom_html, shot = await asyncio.gather(sess.dom_html(), sess.screenshot())
        dom_path = self._capture_dir / f"{seq:04d}.html"
        png_path = self._capture_dir / f"{seq:04d}.png"
        dom_path.write_text(dom_html, encoding="utf-8")
        png_path.write_bytes(base64.b64decode(shot.png_base64))
        return CaptureResult(
            target_id=sess.target_id,
            step_seq=seq,
            dom_path=str(dom_path),
            screenshot_path=str(png_path),
            captured_at_ms=int(time.time() * 1000),
        )


_Connector = Any  # Async callable: (ws_url: str) -> CdpConnection


async def _default_connector(ws_url: str) -> CdpConnection:
    # The default connector enters the connection's async context but
    # holds it open — the manager closes it on detach.
    cm = connect_cdp(ws_url)
    conn = await cm.__aenter__()
    # Stash the context manager on the connection so we can drive
    # __aexit__ from `close()`. The CdpConnection's own close()
    # already cancels the reader and closes the underlying ws, so
    # this is just a precaution against future api drift.
    conn._ctx = cm  # type: ignore[attr-defined]
    return conn


async def _pick_active_target(conn: CdpConnection) -> str:
    result = await conn.send("Target.getTargets", {})
    infos = result.get("targetInfos") or []
    if not isinstance(infos, list):
        raise BrowserError("Target.getTargets returned no targets")
    # Prefer the most recent page target; ignore service workers,
    # iframes, etc.
    pages: list[dict[str, JsonValue]] = [
        info for info in infos if isinstance(info, dict) and info.get("type") == "page"
    ]
    if not pages:
        raise BrowserError("no page targets available — has Chromium opened a tab?")
    target_id = pages[-1].get("targetId")
    if not isinstance(target_id, str):
        raise BrowserError("page target had no targetId")
    return target_id


async def _attach_to_target(conn: CdpConnection, target_id: str) -> str:
    result = await conn.send(
        "Target.attachToTarget",
        {"targetId": target_id, "flatten": True},
    )
    session_id = result.get("sessionId")
    if not isinstance(session_id, str):
        raise BrowserError("Target.attachToTarget did not return a sessionId")
    return session_id


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None
