"""Live MCP connector sessions.

The manager owns one :class:`mcp.ClientSession` per started
connector. Sessions stay alive until the connector is stopped or
the brain shuts down. Tool grants gate every ``call_tool`` —
ungranted invocations raise rather than reaching the wire, so the
audit log records the grant violation instead of leaking the call
to the upstream MCP server.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client

from mcp import ClientSession, StdioServerParameters
from thalyn_brain.mcp.descriptor import ConnectorDescriptor
from thalyn_brain.mcp.registry import ConnectorRecord, ConnectorRegistry

SessionOpener = Callable[[dict[str, Any], dict[str, str]], Awaitable["_LiveSession"]]


class McpError(Exception):
    """Base for connector errors that map to JSON-RPC errors."""


class UnknownConnectorError(McpError):
    """Connector id is not in the catalog or registry."""


class ConnectorNotInstalledError(McpError):
    """Operation requires the connector to be installed first."""


class ConnectorAlreadyInstalledError(McpError):
    """Install was attempted on an id that's already installed."""


class ConnectorNotConfiguredError(McpError):
    """A required secret was not supplied at start time."""


class ToolNotGrantedError(McpError):
    """Tool invocation blocked by the per-connector grant list."""


@dataclass
class InstalledConnector:
    """The wire view of an installed connector with live status."""

    record: ConnectorRecord
    running: bool
    last_error: str | None = None
    advertised_tools: list[dict[str, Any]] | None = None

    def to_wire(self) -> dict[str, Any]:
        wire: dict[str, Any] = self.record.to_wire()
        wire["running"] = self.running
        wire["lastError"] = self.last_error
        if self.advertised_tools is not None:
            wire["liveTools"] = self.advertised_tools
        return wire


@dataclass
class _LiveSession:
    """Internal book-keeping for one active connector session."""

    session: ClientSession
    exit_stack: contextlib.AsyncExitStack
    cached_tools: list[dict[str, Any]] | None = None


class McpManager:
    """Catalog → registry → live-session bridge."""

    def __init__(
        self,
        *,
        registry: ConnectorRegistry,
        catalog: list[ConnectorDescriptor],
        session_opener: SessionOpener | None = None,
    ) -> None:
        self._registry = registry
        self._catalog: dict[str, ConnectorDescriptor] = {d.connector_id: d for d in catalog}
        self._sessions: dict[str, _LiveSession] = {}
        self._errors: dict[str, str] = {}
        self._lock = asyncio.Lock()
        # Tests inject an opener so they can connect against an
        # in-memory MCP server without spawning subprocesses or
        # opening sockets. Production callers fall through to the
        # real stdio / streamable-http openers.
        self._session_opener = session_opener or self._default_open_session

    # ------------------------------------------------------------------
    # Catalog + registry
    # ------------------------------------------------------------------

    def catalog(self) -> list[ConnectorDescriptor]:
        return list(self._catalog.values())

    def descriptor(self, connector_id: str) -> ConnectorDescriptor:
        try:
            return self._catalog[connector_id]
        except KeyError as exc:
            raise UnknownConnectorError(f"unknown connector: {connector_id}") from exc

    async def list_installed(self) -> list[InstalledConnector]:
        records = await self._registry.list_all()
        return [self._snapshot(record) for record in records]

    async def get_installed(self, connector_id: str) -> InstalledConnector | None:
        record = await self._registry.get(connector_id)
        if record is None:
            return None
        return self._snapshot(record)

    def _snapshot(self, record: ConnectorRecord) -> InstalledConnector:
        live = self._sessions.get(record.connector_id)
        return InstalledConnector(
            record=record,
            running=live is not None,
            last_error=self._errors.get(record.connector_id),
            advertised_tools=live.cached_tools if live else None,
        )

    # ------------------------------------------------------------------
    # Install / uninstall (registry only — does not start a session)
    # ------------------------------------------------------------------

    async def install(
        self,
        connector_id: str,
        *,
        granted_tools: list[str] | None = None,
        descriptor_override: dict[str, Any] | None = None,
    ) -> InstalledConnector:
        descriptor = descriptor_override or self.descriptor(connector_id).to_wire()
        existing = await self._registry.get(connector_id)
        if existing is not None:
            raise ConnectorAlreadyInstalledError(f"connector already installed: {connector_id}")
        # Default grants: every advertised non-sensitive tool. The
        # user can grant more from the inspector; sensitive tools
        # require an explicit opt-in to keep "read your inbox" and
        # "send a message" on different sides of the gate.
        if granted_tools is None:
            granted_tools = [
                t["name"]
                for t in descriptor.get("advertisedTools", [])
                if not t.get("sensitive", False)
            ]
        record = await self._registry.upsert(
            connector_id=connector_id,
            descriptor=descriptor,
            granted_tools=list(granted_tools),
            enabled=True,
        )
        return self._snapshot(record)

    async def uninstall(self, connector_id: str) -> bool:
        await self.stop(connector_id, ignore_missing=True)
        return await self._registry.delete(connector_id)

    async def set_grants(self, connector_id: str, granted_tools: list[str]) -> bool:
        return await self._registry.set_grants(connector_id, granted_tools)

    async def set_enabled(self, connector_id: str, enabled: bool) -> bool:
        if not enabled:
            await self.stop(connector_id, ignore_missing=True)
        return await self._registry.set_enabled(connector_id, enabled)

    # ------------------------------------------------------------------
    # Live sessions
    # ------------------------------------------------------------------

    async def start(
        self,
        connector_id: str,
        *,
        secrets: dict[str, str] | None = None,
    ) -> InstalledConnector:
        record = await self._registry.get(connector_id)
        if record is None:
            raise ConnectorNotInstalledError(f"connector not installed: {connector_id}")
        async with self._lock:
            if connector_id in self._sessions:
                return self._snapshot(record)
            descriptor = record.descriptor
            try:
                live = await self._session_opener(descriptor, secrets or {})
            except Exception as exc:
                self._errors[connector_id] = str(exc)
                raise
            self._sessions[connector_id] = live
            self._errors.pop(connector_id, None)
            try:
                tools = await live.session.list_tools()
                live.cached_tools = [_tool_to_wire(t) for t in tools.tools]
            except Exception as exc:
                # The session opened but listing failed; surface the
                # error without tearing the session down — the user
                # may still be able to call known tool names blindly.
                self._errors[connector_id] = f"list_tools failed: {exc}"
        return self._snapshot(record)

    async def stop(self, connector_id: str, *, ignore_missing: bool = False) -> bool:
        async with self._lock:
            live = self._sessions.pop(connector_id, None)
            if live is None:
                if ignore_missing:
                    return False
                raise ConnectorNotInstalledError(f"connector not running: {connector_id}")
            try:
                await live.exit_stack.aclose()
            except Exception as exc:
                self._errors[connector_id] = f"shutdown failed: {exc}"
            return True

    async def shutdown(self) -> None:
        async with self._lock:
            connector_ids = list(self._sessions.keys())
        for connector_id in connector_ids:
            with contextlib.suppress(Exception):
                await self.stop(connector_id, ignore_missing=True)

    async def list_tools(self, connector_id: str) -> list[dict[str, Any]]:
        live = self._sessions.get(connector_id)
        if live is None:
            raise ConnectorNotInstalledError(f"connector not running: {connector_id}")
        tools = await live.session.list_tools()
        live.cached_tools = [_tool_to_wire(t) for t in tools.tools]
        return list(live.cached_tools)

    async def call_tool(
        self,
        connector_id: str,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        record = await self._registry.get(connector_id)
        if record is None:
            raise ConnectorNotInstalledError(f"connector not installed: {connector_id}")
        if not record.enabled:
            raise ConnectorNotInstalledError(f"connector disabled: {connector_id}")
        if tool_name not in record.granted_tools:
            raise ToolNotGrantedError(
                f"tool '{tool_name}' is not granted on connector '{connector_id}'"
            )
        live = self._sessions.get(connector_id)
        if live is None:
            raise ConnectorNotInstalledError(f"connector not running: {connector_id}")
        result = await live.session.call_tool(tool_name, arguments or {})
        return _call_result_to_wire(result)

    # ------------------------------------------------------------------
    # Internal: session opening
    # ------------------------------------------------------------------

    async def _default_open_session(
        self,
        descriptor: dict[str, Any],
        secrets: dict[str, str],
    ) -> _LiveSession:
        transport = descriptor.get("transport")
        stack = contextlib.AsyncExitStack()
        try:
            if transport == "stdio":
                command = descriptor.get("command")
                if not command:
                    raise ConnectorNotConfiguredError("stdio descriptor missing 'command'")
                env_from_secrets = descriptor.get("envFromSecrets", {})
                env: dict[str, str] = {}
                for env_key, secret_key in env_from_secrets.items():
                    value = secrets.get(secret_key)
                    if value is None:
                        raise ConnectorNotConfiguredError(
                            f"missing secret '{secret_key}' for connector"
                        )
                    env[env_key] = value
                params = StdioServerParameters(
                    command=command,
                    args=list(descriptor.get("args", [])),
                    env=env or None,
                )
                streams = await stack.enter_async_context(stdio_client(params))
                read, write = streams
                session = await stack.enter_async_context(ClientSession(read, write))
            elif transport == "streamable_http":
                url = descriptor.get("url")
                if not url:
                    raise ConnectorNotConfiguredError("streamable_http descriptor missing 'url'")
                header_from_secrets = descriptor.get("headerFromSecrets", {})
                header_template = descriptor.get("headerTemplate", "{value}")
                headers: dict[str, str] = {}
                for header_key, secret_key in header_from_secrets.items():
                    value = secrets.get(secret_key)
                    if value is None:
                        raise ConnectorNotConfiguredError(
                            f"missing secret '{secret_key}' for connector"
                        )
                    headers[header_key] = header_template.format(value=value)
                streams = await stack.enter_async_context(
                    streamablehttp_client(url, headers=headers or None)
                )
                read, write, _close = streams
                session = await stack.enter_async_context(ClientSession(read, write))
            else:
                raise ConnectorNotConfiguredError(f"unsupported transport: {transport}")
            await session.initialize()
            return _LiveSession(session=session, exit_stack=stack)
        except BaseException:
            with contextlib.suppress(Exception):
                await stack.aclose()
            raise


def _tool_to_wire(tool: Any) -> dict[str, Any]:
    return {
        "name": getattr(tool, "name", ""),
        "description": getattr(tool, "description", "") or "",
        "inputSchema": getattr(tool, "inputSchema", None),
    }


def _call_result_to_wire(result: Any) -> dict[str, Any]:
    contents: list[dict[str, Any]] = []
    for item in getattr(result, "content", []) or []:
        kind = getattr(item, "type", "text")
        if kind == "text":
            contents.append({"type": "text", "text": getattr(item, "text", "")})
        elif kind == "image":
            contents.append(
                {
                    "type": "image",
                    "data": getattr(item, "data", ""),
                    "mimeType": getattr(item, "mimeType", "application/octet-stream"),
                }
            )
        else:
            contents.append({"type": kind, "raw": repr(item)})
    return {
        "content": contents,
        "isError": bool(getattr(result, "isError", False)),
    }


__all__ = [
    "ConnectorAlreadyInstalledError",
    "ConnectorNotConfiguredError",
    "ConnectorNotInstalledError",
    "InstalledConnector",
    "McpError",
    "McpManager",
    "ToolNotGrantedError",
    "UnknownConnectorError",
]
