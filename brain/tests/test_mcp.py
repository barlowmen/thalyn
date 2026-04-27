"""Tests for the MCP connector subsystem.

Covers the catalog → registry → live-session bridge using an
in-process FastMCP server. Real stdio / streamable-http transports
are tested transitively in CI by the integration smoke against the
brain sidecar; these unit tests focus on the manager's wire
contract and grant gating.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session
from thalyn_brain.mcp import (
    ConnectorAlreadyInstalledError,
    ConnectorRegistry,
    McpManager,
    ToolNotGrantedError,
    UnknownConnectorError,
    builtin_catalog,
)
from thalyn_brain.mcp.descriptor import (
    ConnectorAuth,
    ConnectorDescriptor,
    ConnectorTool,
)
from thalyn_brain.mcp.manager import _LiveSession


def _fake_server() -> FastMCP:
    """A toy MCP server with a couple of tools to exercise dispatch."""
    server = FastMCP("test-mcp-server")

    @server.tool()
    def echo(text: str) -> str:
        return f"echo:{text}"

    @server.tool()
    def shout(text: str) -> str:
        return text.upper()

    return server


def _fake_descriptor() -> ConnectorDescriptor:
    return ConnectorDescriptor(
        connector_id="fake",
        display_name="Fake",
        summary="In-memory test connector",
        category="other",
        transport="stdio",
        command="not-a-real-command",
        args=(),
        required_secrets=(ConnectorAuth(key="api_key", label="API key", description="..."),),
        advertised_tools=(
            ConnectorTool("echo", "Echo back."),
            ConnectorTool("shout", "Shout back.", sensitive=True),
        ),
    )


@contextlib.asynccontextmanager
async def _bound_manager(tmp_path: Path) -> AsyncIterator[tuple[McpManager, FastMCP]]:
    """Yield a manager whose session opener returns an in-memory link."""
    server = _fake_server()
    registry = ConnectorRegistry(data_dir=tmp_path)

    # The opener is invoked under the manager's lock; we ignore the
    # descriptor / secret args and stand up a fresh in-memory session
    # against the FastMCP instance every time. The session is left
    # registered in an AsyncExitStack so shutdown closes it cleanly.
    async def opener(_descriptor: dict[str, Any], _secrets: dict[str, str]) -> _LiveSession:
        stack = contextlib.AsyncExitStack()
        session = await stack.enter_async_context(
            create_connected_server_and_client_session(server._mcp_server)
        )
        return _LiveSession(session=session, exit_stack=stack)

    manager = McpManager(
        registry=registry,
        catalog=[_fake_descriptor()],
        session_opener=opener,
    )
    try:
        yield manager, server
    finally:
        await manager.shutdown()


@pytest.mark.asyncio
async def test_catalog_lists_descriptors(tmp_path: Path) -> None:
    async with _bound_manager(tmp_path) as (manager, _):
        descriptors = manager.catalog()
        assert [d.connector_id for d in descriptors] == ["fake"]


@pytest.mark.asyncio
async def test_install_seeds_default_grants(tmp_path: Path) -> None:
    async with _bound_manager(tmp_path) as (manager, _):
        installed = await manager.install("fake")
    # Sensitive tools default to ungranted; the user opts them in.
    assert installed.record.granted_tools == ["echo"]
    assert installed.running is False


@pytest.mark.asyncio
async def test_install_rejects_unknown(tmp_path: Path) -> None:
    async with _bound_manager(tmp_path) as (manager, _):
        with pytest.raises(UnknownConnectorError):
            await manager.install("not-a-connector")


@pytest.mark.asyncio
async def test_install_is_idempotent_per_id(tmp_path: Path) -> None:
    async with _bound_manager(tmp_path) as (manager, _):
        await manager.install("fake")
        with pytest.raises(ConnectorAlreadyInstalledError):
            await manager.install("fake")


@pytest.mark.asyncio
async def test_set_grants_overwrites(tmp_path: Path) -> None:
    async with _bound_manager(tmp_path) as (manager, _):
        await manager.install("fake")
        updated = await manager.set_grants("fake", ["echo", "shout"])
        assert updated is True
        installed = await manager.get_installed("fake")
        assert installed is not None
        assert installed.record.granted_tools == ["echo", "shout"]


@pytest.mark.asyncio
async def test_uninstall_removes_record(tmp_path: Path) -> None:
    async with _bound_manager(tmp_path) as (manager, _):
        await manager.install("fake")
        deleted = await manager.uninstall("fake")
        assert deleted is True
        assert await manager.get_installed("fake") is None


@pytest.mark.asyncio
async def test_call_tool_requires_grant(tmp_path: Path) -> None:
    async with _bound_manager(tmp_path) as (manager, _):
        await manager.install("fake")
        await manager.start("fake")
        # echo is granted by default; calling it succeeds.
        result = await manager.call_tool("fake", "echo", {"text": "hi"})
        assert result["isError"] is False
        assert result["content"][0]["type"] == "text"
        assert result["content"][0]["text"] == "echo:hi"
        # shout is sensitive — ungranted by default.
        with pytest.raises(ToolNotGrantedError):
            await manager.call_tool("fake", "shout", {"text": "hi"})


@pytest.mark.asyncio
async def test_call_tool_after_grant_promotion(tmp_path: Path) -> None:
    async with _bound_manager(tmp_path) as (manager, _):
        await manager.install("fake")
        await manager.start("fake")
        await manager.set_grants("fake", ["echo", "shout"])
        result = await manager.call_tool("fake", "shout", {"text": "hi"})
        assert result["content"][0]["text"] == "HI"


@pytest.mark.asyncio
async def test_list_tools_returns_live_set(tmp_path: Path) -> None:
    async with _bound_manager(tmp_path) as (manager, _):
        await manager.install("fake")
        await manager.start("fake")
        tools = await manager.list_tools("fake")
        names = {t["name"] for t in tools}
        assert {"echo", "shout"} <= names


@pytest.mark.asyncio
async def test_disable_stops_live_session(tmp_path: Path) -> None:
    async with _bound_manager(tmp_path) as (manager, _):
        await manager.install("fake")
        await manager.start("fake")
        await manager.set_enabled("fake", False)
        installed = await manager.get_installed("fake")
        assert installed is not None
        assert installed.running is False
        assert installed.record.enabled is False


def test_builtin_catalog_shape() -> None:
    descriptors = builtin_catalog()
    ids = [d.connector_id for d in descriptors]
    assert ids == ["slack", "office", "google_calendar"]
    for descriptor in descriptors:
        assert descriptor.first_party is True
        assert descriptor.required_secrets, f"{descriptor.connector_id} has no required secrets"
        assert descriptor.advertised_tools, f"{descriptor.connector_id} has no advertised tools"
