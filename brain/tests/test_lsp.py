"""LSP scaffolding — manager + JSON-RPC bindings."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from thalyn_brain.lsp import (
    DEFAULT_LSP_COMMANDS,
    LspManager,
    LspNotAvailableError,
)
from thalyn_brain.lsp_rpc import register_lsp_methods
from thalyn_brain.rpc import Dispatcher

# A standalone Python script we spawn as the "LSP server" for tests.
# It echoes every incoming framed message back with the same id and
# emits one server-initiated notification on startup so the
# notification path is exercised. Using a real subprocess ensures we
# cover the Content-Length framing, stdin/stdout drain, and process
# teardown — not just an in-process mock.
STUB_SCRIPT = r"""
import json
import sys


def write_msg(payload: dict) -> None:
    body = json.dumps(payload).encode("utf-8")
    sys.stdout.buffer.write(b"Content-Length: %d\r\n\r\n" % len(body))
    sys.stdout.buffer.write(body)
    sys.stdout.buffer.flush()


def read_msg() -> dict | None:
    headers: dict[str, str] = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        if line in (b"\r\n", b"\n"):
            break
        decoded = line.decode("ascii", errors="replace").strip()
        if ":" in decoded:
            key, _, value = decoded.partition(":")
            headers[key.strip().lower()] = value.strip()
    length = int(headers.get("content-length", "0"))
    body = sys.stdin.buffer.read(length)
    return json.loads(body)


# Server-initiated notification on startup.
write_msg({"jsonrpc": "2.0", "method": "stub/ready", "params": {"ok": True}})

while True:
    msg = read_msg()
    if msg is None:
        break
    if "id" in msg:
        write_msg({"jsonrpc": "2.0", "id": msg["id"], "result": {"echoed": msg}})
"""


@pytest.fixture
def stub_command(tmp_path: Path) -> list[str]:
    script = tmp_path / "stub_lsp.py"
    script.write_text(STUB_SCRIPT)
    return [sys.executable, str(script)]


# ---------------------------------------------------------------------------
# LspManager
# ---------------------------------------------------------------------------


async def test_manager_start_then_send_then_stop(
    stub_command: list[str],
) -> None:
    captured: list[tuple[str, Any]] = []

    async def notify(method: str, params: Any) -> None:
        captured.append((method, params))

    manager = LspManager(commands={"toy": stub_command}, notify=notify)
    session = await manager.start("toy")
    assert session.language == "toy"
    assert session.command == stub_command

    # The server emits a stub/ready notification on startup; wait
    # briefly for the reader to pick it up.
    await _await_notification(captured, "stub/ready")

    await manager.send(
        session.session_id,
        {"jsonrpc": "2.0", "id": 1, "method": "echo", "params": {"x": 1}},
    )
    await _await_notification_with(
        captured, lambda payload: payload.get("message", {}).get("id") == 1
    )

    assert any(
        method == "lsp.message"
        and payload.get("message", {}).get("id") == 1
        and payload.get("sessionId") == session.session_id
        for method, payload in captured
    )

    stopped = await manager.stop(session.session_id)
    assert stopped is True


async def test_stop_unknown_session_returns_false(
    stub_command: list[str],
) -> None:
    manager = LspManager(commands={"toy": stub_command})
    assert await manager.stop("missing") is False


async def test_start_raises_when_command_missing() -> None:
    manager = LspManager(commands={"toy": ["/no/such/binary/exists/here"]})
    with pytest.raises(LspNotAvailableError):
        await manager.start("toy")


async def test_default_commands_cover_typescript_and_python() -> None:
    # We don't require these to be installed — just that the manager
    # ships them as the default surface, so a packaged Thalyn doesn't
    # need per-host configuration.
    assert "typescript" in DEFAULT_LSP_COMMANDS
    assert "python" in DEFAULT_LSP_COMMANDS


# ---------------------------------------------------------------------------
# JSON-RPC bindings
# ---------------------------------------------------------------------------


async def test_lsp_start_send_stop_via_dispatcher(
    stub_command: list[str],
) -> None:
    dispatcher = Dispatcher()
    manager = LspManager(commands={"toy": stub_command})
    register_lsp_methods(dispatcher, manager)

    captured: list[tuple[str, Any]] = []

    async def notify(method: str, params: Any) -> None:
        captured.append((method, params))

    start_response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "lsp.start",
            "params": {"language": "toy"},
        },
        notify,
    )
    assert start_response is not None
    session_id = start_response["result"]["sessionId"]
    assert session_id.startswith("lsp_")

    await _await_notification(captured, "stub/ready")

    send_response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "lsp.send",
            "params": {
                "sessionId": session_id,
                "message": {
                    "jsonrpc": "2.0",
                    "id": 7,
                    "method": "ping",
                    "params": {},
                },
            },
        },
        notify,
    )
    assert send_response is not None
    assert send_response["result"]["queued"] is True
    await _await_notification_with(
        captured, lambda payload: payload.get("message", {}).get("id") == 7
    )

    list_response = await dispatcher.handle(
        {"jsonrpc": "2.0", "id": 3, "method": "lsp.list", "params": {}},
        notify,
    )
    assert list_response is not None
    sessions = list_response["result"]["sessions"]
    assert len(sessions) == 1
    assert sessions[0]["sessionId"] == session_id

    stop_response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "lsp.stop",
            "params": {"sessionId": session_id},
        },
        notify,
    )
    assert stop_response is not None
    assert stop_response["result"]["stopped"] is True


async def test_lsp_start_with_unknown_language_errors() -> None:
    dispatcher = Dispatcher()
    manager = LspManager(commands={})
    register_lsp_methods(dispatcher, manager)

    async def notify(method: str, params: Any) -> None:
        del method, params

    response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "lsp.start",
            "params": {"language": "klingon"},
        },
        notify,
    )
    assert response is not None
    assert "error" in response
    assert "no LSP command configured" in response["error"]["message"]


async def test_lsp_start_when_binary_missing_returns_not_available() -> None:
    dispatcher = Dispatcher()
    manager = LspManager(commands={"toy": ["/no/such/binary"]})
    register_lsp_methods(dispatcher, manager)

    async def notify(method: str, params: Any) -> None:
        del method, params

    response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "lsp.start",
            "params": {"language": "toy"},
        },
        notify,
    )
    assert response is not None
    err = response["error"]
    assert err["data"]["reason"] == "not-available"


async def test_lsp_send_with_invalid_session_errors(
    stub_command: list[str],
) -> None:
    dispatcher = Dispatcher()
    manager = LspManager(commands={"toy": stub_command})
    register_lsp_methods(dispatcher, manager)

    async def notify(method: str, params: Any) -> None:
        del method, params

    response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "lsp.send",
            "params": {
                "sessionId": "missing",
                "message": {"jsonrpc": "2.0", "id": 1, "method": "ping"},
            },
        },
        notify,
    )
    assert response is not None
    assert "unknown LSP session" in response["error"]["message"]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


_DEFAULT_DEADLINE = 2.0


async def _await_notification(captured: list[tuple[str, Any]], method: str) -> None:
    deadline = asyncio.get_event_loop().time() + _DEFAULT_DEADLINE
    while asyncio.get_event_loop().time() < deadline:
        if any(
            m == "lsp.message" and p.get("message", {}).get("method") == method for m, p in captured
        ):
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"timed out waiting for notification: {method}")


async def _await_notification_with(
    captured: list[tuple[str, Any]],
    predicate: Any,
) -> None:
    deadline = asyncio.get_event_loop().time() + _DEFAULT_DEADLINE
    while asyncio.get_event_loop().time() < deadline:
        for method, payload in captured:
            if method != "lsp.message":
                continue
            try:
                if predicate(payload):
                    return
            except Exception:
                continue
        await asyncio.sleep(0.01)
    raise AssertionError("timed out waiting for predicate notification")


# Silence: json import is used inside STUB_SCRIPT only via the
# subprocess; the local module use is in helpers above.
del json
