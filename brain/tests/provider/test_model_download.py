"""Local-model availability + pull tests."""

from __future__ import annotations

import json
from typing import Any

import httpx
from thalyn_brain.provider.model_download import (
    check_mlx_model,
    check_ollama_model,
    pull_ollama_model,
)


def _ndjson(records: list[dict[str, Any]]) -> bytes:
    return ("\n".join(json.dumps(r) for r in records) + "\n").encode("utf-8")


def _factory_for(handler: Any) -> Any:
    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    return factory


# ---------------------------------------------------------------------------
# check_ollama_model
# ---------------------------------------------------------------------------


async def test_check_returns_available_when_exact_tag_listed() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"models": [{"name": "qwen3-coder:7b"}, {"name": "llama3"}]},
        )

    status = await check_ollama_model(
        base_url="http://localhost:11434",
        model="qwen3-coder:7b",
        client_factory=_factory_for(handler),
    )
    assert status.state == "available"
    assert status.model == "qwen3-coder:7b"


async def test_check_resolves_root_to_a_tagged_entry() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": [{"name": "qwen3-coder:7b"}]})

    status = await check_ollama_model(
        base_url="http://localhost:11434",
        model="qwen3-coder",
        client_factory=_factory_for(handler),
    )
    assert status.state == "available"
    assert status.detail == "resolved tag: qwen3-coder:7b"


async def test_check_returns_missing_when_not_in_catalogue() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": [{"name": "llama3"}]})

    status = await check_ollama_model(
        base_url="http://localhost:11434",
        model="qwen3-coder",
        client_factory=_factory_for(handler),
    )
    assert status.state == "missing"


async def test_check_returns_unknown_on_transport_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("ollama not running")

    status = await check_ollama_model(
        base_url="http://localhost:11434",
        model="qwen3-coder",
        client_factory=_factory_for(handler),
    )
    assert status.state == "unknown"
    assert "unreachable" in (status.detail or "")


async def test_check_handles_non_200_gracefully() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, content=b"oops")

    status = await check_ollama_model(
        base_url="http://localhost:11434",
        model="qwen3-coder",
        client_factory=_factory_for(handler),
    )
    assert status.state == "unknown"
    assert "500" in (status.detail or "")


# ---------------------------------------------------------------------------
# pull_ollama_model
# ---------------------------------------------------------------------------


async def test_pull_yields_progress_until_success() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=_ndjson(
                [
                    {"status": "pulling manifest"},
                    {
                        "status": "downloading",
                        "digest": "abc",
                        "completed": 100,
                        "total": 1000,
                    },
                    {
                        "status": "downloading",
                        "digest": "abc",
                        "completed": 1000,
                        "total": 1000,
                    },
                    {"status": "verifying sha256"},
                    {"status": "success"},
                ]
            ),
        )

    progresses = [
        progress
        async for progress in pull_ollama_model(
            base_url="http://localhost:11434",
            model="qwen3-coder",
            client_factory=_factory_for(handler),
        )
    ]
    assert progresses[0].status == "pulling manifest"
    assert progresses[1].digest == "abc"
    assert progresses[1].completed == 100
    assert progresses[1].total == 1000
    assert progresses[-1].status == "success"


async def test_pull_yields_terminal_error_on_non_200() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, content=b"model not found upstream")

    progresses = [
        progress
        async for progress in pull_ollama_model(
            base_url="http://localhost:11434",
            model="ghost",
            client_factory=_factory_for(handler),
        )
    ]
    assert len(progresses) == 1
    assert progresses[0].status.startswith("error: ollama returned 404")


async def test_pull_yields_terminal_error_on_transport_failure() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("daemon offline")

    progresses = [
        progress
        async for progress in pull_ollama_model(
            base_url="http://localhost:11434",
            model="qwen3-coder",
            client_factory=_factory_for(handler),
        )
    ]
    assert len(progresses) == 1
    assert "unreachable" in progresses[0].status


async def test_pull_short_circuits_on_in_stream_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=_ndjson(
                [
                    {"status": "downloading", "digest": "abc"},
                    {"error": "no such tag"},
                    {"status": "should not reach"},
                ]
            ),
        )

    progresses = [
        progress
        async for progress in pull_ollama_model(
            base_url="http://localhost:11434",
            model="qwen3-coder",
            client_factory=_factory_for(handler),
        )
    ]
    statuses = [progress.status for progress in progresses]
    assert "should not reach" not in statuses
    assert any(status.startswith("error:") for status in statuses)


# ---------------------------------------------------------------------------
# check_mlx_model
# ---------------------------------------------------------------------------


def test_check_mlx_returns_unknown_with_descriptive_detail() -> None:
    status = check_mlx_model(model="mlx-community/Qwen3")
    assert status.state == "unknown"
    assert "first stream" in (status.detail or "")
