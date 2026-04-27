"""Thin Chrome DevTools Protocol client over a WebSocket.

A v0.13 brain that drives a Rust-spawned Chromium needs ~ten CDP
methods: navigate, screenshot, an `Input.dispatch*` family, a couple
of `Runtime.evaluate` calls for text/click hit-tests. We don't need
the full Playwright / Stagehand / browser-use surface, so we ship a
focused async client that knows how to:

* connect to a `ws://` endpoint Chromium wrote to its
  `DevToolsActivePort` file,
* send a request and route the matching response back to the caller
  (auto-incrementing id, per-id ``Future``),
* fan out events to subscribers without blocking the request path.

Session-prefixed messages (so the same WS can drive multiple
targets, the same way Playwright multiplexes) are handled by passing
``session_id`` to :meth:`CdpConnection.send`.

This file is deliberately self-contained — it depends only on
:mod:`websockets`, which we add to brain deps in this commit. If we
ever need richer features (frame trees, network-domain auth flows,
etc.) we can layer them on top, or migrate to Playwright; the surface
stays narrow until then.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from typing import Any

import websockets
from websockets.asyncio.client import ClientConnection

JsonValue = Any


class CdpError(RuntimeError):
    """Generic CDP transport / protocol error."""


class CdpClosedError(CdpError):
    """The connection closed before the request completed."""


class CdpRemoteError(CdpError):
    """Chromium returned an error response for our request."""

    def __init__(self, code: int, message: str, data: JsonValue = None) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.data = data


@dataclass
class CdpEvent:
    """One CDP event pushed by Chromium."""

    method: str
    params: dict[str, JsonValue] = field(default_factory=dict)
    session_id: str | None = None


EventListener = Callable[[CdpEvent], None]


class CdpConnection:
    """An open WebSocket connection to a CDP endpoint.

    Build via :func:`connect` (or :meth:`open_session` for a
    target-attached child connection); always use as an async context
    manager so the reader task is cancelled cleanly on close.
    """

    def __init__(self, ws: ClientConnection) -> None:
        self._ws = ws
        self._next_id = 1
        self._pending: dict[int, asyncio.Future[JsonValue]] = {}
        self._listeners: list[EventListener] = []
        self._closed = asyncio.Event()
        self._reader_task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> CdpConnection:
        self._reader_task = asyncio.create_task(self._reader_loop())
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        await self.close()

    async def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        with suppress(Exception):
            await self._ws.close()
        if self._reader_task is not None:
            self._reader_task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await self._reader_task
        self._fail_pending(CdpClosedError("connection closed"))

    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    def add_listener(self, listener: EventListener) -> None:
        """Subscribe to CDP events; never blocks the caller."""
        self._listeners.append(listener)

    def remove_listener(self, listener: EventListener) -> None:
        with suppress(ValueError):
            self._listeners.remove(listener)

    async def send(
        self,
        method: str,
        params: dict[str, JsonValue] | None = None,
        *,
        session_id: str | None = None,
        timeout: float = 30.0,  # noqa: ASYNC109 — explicit per-call timeout is part of the surface
    ) -> JsonValue:
        """Send a CDP command and await the matching response.

        Raises :class:`CdpClosedError` if the connection drops before
        the response arrives, or :class:`CdpRemoteError` if Chromium
        returns an error envelope.
        """
        if self._closed.is_set():
            raise CdpClosedError("connection is closed")

        message_id = self._next_id
        self._next_id += 1
        envelope: dict[str, JsonValue] = {
            "id": message_id,
            "method": method,
            "params": params or {},
        }
        if session_id is not None:
            envelope["sessionId"] = session_id

        loop = asyncio.get_running_loop()
        fut: asyncio.Future[JsonValue] = loop.create_future()
        self._pending[message_id] = fut
        try:
            await self._ws.send(json.dumps(envelope))
            return await asyncio.wait_for(fut, timeout=timeout)
        except TimeoutError as exc:
            raise CdpError(f"timed out after {timeout}s waiting for {method!r}") from exc
        finally:
            self._pending.pop(message_id, None)

    async def _reader_loop(self) -> None:
        """Pull messages off the WS until it closes; route them."""
        try:
            async for raw in self._ws:
                # We accept both str and bytes — Chromium sends text but
                # the websockets library may surface bytes if a binary
                # frame ever appears.
                text = raw if isinstance(raw, str) else raw.decode("utf-8", errors="replace")
                try:
                    message = json.loads(text)
                except json.JSONDecodeError:
                    continue
                self._dispatch(message)
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self._closed.set()
            self._fail_pending(CdpClosedError("connection closed by remote"))

    def _dispatch(self, message: dict[str, JsonValue]) -> None:
        message_id = message.get("id")
        if isinstance(message_id, int):
            fut = self._pending.get(message_id)
            if fut is None:
                return
            err_value = message.get("error")
            if isinstance(err_value, dict):
                code_value = err_value.get("code")
                code = code_value if isinstance(code_value, int) else -1
                msg_value = err_value.get("message")
                msg = msg_value if isinstance(msg_value, str) else "cdp error"
                fut.set_exception(CdpRemoteError(code, msg, err_value.get("data")))
            else:
                fut.set_result(message.get("result", {}))
            return

        method = message.get("method")
        if isinstance(method, str):
            params = message.get("params") or {}
            if not isinstance(params, dict):
                params = {}
            session_id_value = message.get("sessionId")
            session_id = session_id_value if isinstance(session_id_value, str) else None
            event = CdpEvent(method=method, params=params, session_id=session_id)
            for listener in list(self._listeners):
                try:
                    listener(event)
                except Exception:
                    # Listeners must not break the reader loop.
                    continue

    def _fail_pending(self, exc: BaseException) -> None:
        pending = self._pending
        self._pending = {}
        for fut in pending.values():
            if not fut.done():
                fut.set_exception(exc)


@asynccontextmanager
async def connect(ws_url: str, *, open_timeout: float = 10.0) -> AsyncIterator[CdpConnection]:
    """Open a CDP WebSocket connection.

    Usage::

        async with connect(ws_url) as cdp:
            await cdp.send("Page.navigate", {"url": "https://example.com"})
    """
    try:
        ws = await asyncio.wait_for(
            websockets.connect(
                ws_url,
                # CDP messages can be large (full DOM dumps, base64
                # screenshots) — disable the default frame-size cap.
                max_size=None,
                ping_interval=None,
            ),
            timeout=open_timeout,
        )
    except (TimeoutError, OSError) as exc:
        raise CdpError(f"could not open CDP WebSocket at {ws_url}: {exc}") from exc

    conn = CdpConnection(ws)
    try:
        async with conn as opened:
            yield opened
    finally:
        await conn.close()
