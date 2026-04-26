"""Tests for the NDJSON transport."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from thalyn_brain.rpc import build_default_dispatcher
from thalyn_brain.transport import serve_stream


async def _drive(lines: list[bytes]) -> list[dict[str, Any]]:
    reader = asyncio.StreamReader()
    for line in lines:
        reader.feed_data(line)
    reader.feed_eof()

    sink: list[bytes] = []

    class _Writer:
        def write(self, data: bytes) -> None:
            sink.append(data)

        async def drain(self) -> None:
            return None

    writer = _Writer()
    await serve_stream(reader, writer, build_default_dispatcher())  # type: ignore[arg-type]
    raw = b"".join(sink).decode("utf-8")
    return [json.loads(line) for line in raw.splitlines() if line]


async def test_round_trip_single_request() -> None:
    responses = await _drive([b'{"jsonrpc":"2.0","id":1,"method":"ping"}\n'])
    assert len(responses) == 1
    assert responses[0]["id"] == 1
    assert responses[0]["result"]["pong"] is True


async def test_round_trip_multiple_requests() -> None:
    responses = await _drive(
        [
            b'{"jsonrpc":"2.0","id":1,"method":"ping"}\n',
            b'{"jsonrpc":"2.0","id":2,"method":"ping"}\n',
        ]
    )
    assert [r["id"] for r in responses] == [1, 2]


async def test_blank_lines_are_ignored() -> None:
    responses = await _drive([b"\n", b'{"jsonrpc":"2.0","id":3,"method":"ping"}\n', b"\n"])
    assert len(responses) == 1
    assert responses[0]["id"] == 3


async def test_invalid_json_yields_parse_error() -> None:
    responses = await _drive([b"not json\n"])
    assert len(responses) == 1
    assert responses[0]["error"]["code"] == -32700
    assert responses[0]["id"] is None


async def test_notification_produces_no_response() -> None:
    responses = await _drive([b'{"jsonrpc":"2.0","method":"ping"}\n'])
    assert responses == []


async def test_request_after_notification_still_responds() -> None:
    responses = await _drive(
        [
            b'{"jsonrpc":"2.0","method":"ping"}\n',
            b'{"jsonrpc":"2.0","id":4,"method":"ping"}\n',
        ]
    )
    assert len(responses) == 1
    assert responses[0]["id"] == 4


@pytest.mark.parametrize(
    "payload, want_code",
    [
        (b'{"jsonrpc":"2.0","id":1}\n', -32600),
        (b'{"jsonrpc":"2.0","id":1,"method":"ping","params":[1,2]}\n', -32602),
        (b'{"jsonrpc":"2.0","id":1,"method":"unknown"}\n', -32601),
    ],
)
async def test_known_error_codes(payload: bytes, want_code: int) -> None:
    responses = await _drive([payload])
    assert responses[0]["error"]["code"] == want_code
