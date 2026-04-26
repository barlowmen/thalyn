"""Brain-side terminal observer + agent-attach tool."""

from __future__ import annotations

from typing import Any

import pytest
from thalyn_brain.rpc import Dispatcher
from thalyn_brain.terminal_observer import (
    PER_SESSION_BUFFER_CHARS,
    TerminalObserver,
)
from thalyn_brain.terminal_rpc import register_terminal_methods
from thalyn_brain.terminal_tool import (
    TOOL_DESCRIPTION,
    TOOL_NAME,
    TerminalAttachUnavailable,
    terminal_attach,
    tool_spec,
)

# ---------------------------------------------------------------------------
# TerminalObserver
# ---------------------------------------------------------------------------


async def test_observe_then_read_returns_recent_buffer() -> None:
    observer = TerminalObserver()
    await observer.observe("term_a", 1, "hello, ")
    await observer.observe("term_a", 2, "world\n")
    snap = await observer.read("term_a")
    assert snap is not None
    assert snap.data == "hello, world\n"
    assert snap.last_seq == 2
    assert snap.session_id == "term_a"


async def test_read_without_session_id_returns_most_recent() -> None:
    observer = TerminalObserver()
    await observer.observe("term_a", 1, "alpha")
    await observer.observe("term_b", 1, "beta")
    snap = await observer.read()
    assert snap is not None
    assert snap.session_id == "term_b"


async def test_read_unknown_session_returns_none() -> None:
    observer = TerminalObserver()
    assert await observer.read("nope") is None


async def test_buffer_truncates_to_per_session_cap() -> None:
    observer = TerminalObserver()
    big = "x" * (PER_SESSION_BUFFER_CHARS + 200)
    await observer.observe("t", 1, big)
    snap = await observer.read("t")
    assert snap is not None
    assert len(snap.data) == PER_SESSION_BUFFER_CHARS


async def test_max_chars_caps_returned_window() -> None:
    observer = TerminalObserver()
    await observer.observe("t", 1, "0123456789")
    snap = await observer.read("t", max_chars=4)
    assert snap is not None
    assert snap.data == "6789"


async def test_forget_drops_session_state() -> None:
    observer = TerminalObserver()
    await observer.observe("t", 1, "x")
    forgotten = await observer.forget("t")
    assert forgotten is True
    assert await observer.read("t") is None


async def test_listener_fires_on_observe() -> None:
    observer = TerminalObserver()
    seen: list[tuple[str, str]] = []

    async def listener(session_id: str, data: str) -> None:
        seen.append((session_id, data))

    observer.add_listener(listener)
    await observer.observe("t", 1, "ping")
    assert seen == [("t", "ping")]


async def test_list_sessions_orders_most_recent_first() -> None:
    observer = TerminalObserver()
    await observer.observe("t1", 1, "a")
    await observer.observe("t2", 1, "b")
    await observer.observe("t1", 2, "c")  # nudges t1 back to the top
    sessions = await observer.list_sessions()
    assert [s["sessionId"] for s in sessions] == ["t1", "t2"]


# ---------------------------------------------------------------------------
# JSON-RPC bindings
# ---------------------------------------------------------------------------


async def test_dispatcher_observe_then_read_round_trip() -> None:
    observer = TerminalObserver()
    dispatcher = Dispatcher()
    register_terminal_methods(dispatcher, observer)

    async def notify(method: str, params: Any) -> None:
        del method, params

    await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "terminal.observe",
            "params": {"sessionId": "t", "seq": 1, "data": "hi"},
        },
        notify,
    )
    response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "terminal.read",
            "params": {"sessionId": "t"},
        },
        notify,
    )
    assert response is not None
    snap = response["result"]["snapshot"]
    assert snap["data"] == "hi"
    assert snap["lastSeq"] == 1


async def test_dispatcher_read_without_session_id_uses_most_recent() -> None:
    observer = TerminalObserver()
    dispatcher = Dispatcher()
    register_terminal_methods(dispatcher, observer)

    async def notify(method: str, params: Any) -> None:
        del method, params

    await observer.observe("t1", 1, "x")
    await observer.observe("t2", 1, "y")

    response = await dispatcher.handle(
        {"jsonrpc": "2.0", "id": 1, "method": "terminal.read", "params": {}},
        notify,
    )
    assert response is not None
    assert response["result"]["snapshot"]["sessionId"] == "t2"


async def test_dispatcher_observe_rejects_non_string_data() -> None:
    observer = TerminalObserver()
    dispatcher = Dispatcher()
    register_terminal_methods(dispatcher, observer)

    async def notify(method: str, params: Any) -> None:
        del method, params

    response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "terminal.observe",
            "params": {"sessionId": "t", "seq": 1, "data": 42},
        },
        notify,
    )
    assert response is not None
    assert "data must be a string" in response["error"]["message"]


# ---------------------------------------------------------------------------
# Agent attach tool
# ---------------------------------------------------------------------------


async def test_terminal_attach_returns_recent_output() -> None:
    observer = TerminalObserver()
    await observer.observe("t", 1, "agent reads this")
    result = await terminal_attach(observer, session_id="t")
    assert result.session_id == "t"
    assert "agent reads" in result.data


async def test_terminal_attach_raises_when_no_sessions() -> None:
    observer = TerminalObserver()
    with pytest.raises(TerminalAttachUnavailable):
        await terminal_attach(observer)


async def test_terminal_attach_raises_for_unknown_session() -> None:
    observer = TerminalObserver()
    await observer.observe("real", 1, "x")
    with pytest.raises(TerminalAttachUnavailable):
        await terminal_attach(observer, session_id="ghost")


def test_tool_spec_carries_name_description_schema() -> None:
    spec = tool_spec()
    assert spec["name"] == TOOL_NAME
    assert spec["description"] == TOOL_DESCRIPTION
    assert spec["input_schema"]["type"] == "object"
    assert "sessionId" in spec["input_schema"]["properties"]
