"""JSON-RPC surface for the MCP connector subsystem.

The renderer drives the marketplace + grants UI through these
methods; the orchestrator's tool adapter (a follow-up wiring) calls
``mcp.call_tool`` to execute a granted tool. Secrets travel inline
on the start request — the Rust core fetches them from the OS
keychain and forwards them — so the brain never persists them.
"""

from __future__ import annotations

from typing import Any

from thalyn_brain.mcp import (
    ConnectorAlreadyInstalledError,
    ConnectorNotConfiguredError,
    ConnectorNotInstalledError,
    McpError,
    McpManager,
    ToolNotGrantedError,
    UnknownConnectorError,
)
from thalyn_brain.rpc import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    Dispatcher,
    JsonValue,
    RpcError,
    RpcParams,
)


def register_mcp_methods(dispatcher: Dispatcher, manager: McpManager) -> None:
    async def mcp_catalog(_: RpcParams) -> JsonValue:
        return {"connectors": [d.to_wire() for d in manager.catalog()]}

    async def mcp_list(_: RpcParams) -> JsonValue:
        installed = await manager.list_installed()
        return {"installed": [item.to_wire() for item in installed]}

    async def mcp_install(params: RpcParams) -> JsonValue:
        connector_id = _require_str(params, "connectorId")
        granted = params.get("grantedTools")
        granted_list: list[str] | None = None
        if granted is not None:
            if not isinstance(granted, list) or not all(isinstance(t, str) for t in granted):
                raise RpcError(
                    code=INVALID_PARAMS,
                    message="grantedTools must be an array of strings",
                )
            granted_list = list(granted)
        try:
            installed = await manager.install(connector_id, granted_tools=granted_list)
        except UnknownConnectorError as exc:
            raise RpcError(code=INVALID_PARAMS, message=str(exc)) from exc
        except ConnectorAlreadyInstalledError as exc:
            raise RpcError(code=INVALID_PARAMS, message=str(exc)) from exc
        return installed.to_wire()

    async def mcp_uninstall(params: RpcParams) -> JsonValue:
        connector_id = _require_str(params, "connectorId")
        deleted = await manager.uninstall(connector_id)
        return {"uninstalled": deleted}

    async def mcp_set_grants(params: RpcParams) -> JsonValue:
        connector_id = _require_str(params, "connectorId")
        granted = params.get("grantedTools")
        if not isinstance(granted, list) or not all(isinstance(t, str) for t in granted):
            raise RpcError(
                code=INVALID_PARAMS,
                message="grantedTools must be an array of strings",
            )
        updated = await manager.set_grants(connector_id, list(granted))
        if not updated:
            raise RpcError(
                code=INVALID_PARAMS,
                message=f"connector not installed: {connector_id}",
            )
        return {"updated": True}

    async def mcp_set_enabled(params: RpcParams) -> JsonValue:
        connector_id = _require_str(params, "connectorId")
        enabled_value = params.get("enabled")
        if not isinstance(enabled_value, bool):
            raise RpcError(code=INVALID_PARAMS, message="enabled must be a boolean")
        updated = await manager.set_enabled(connector_id, enabled_value)
        if not updated:
            raise RpcError(
                code=INVALID_PARAMS,
                message=f"connector not installed: {connector_id}",
            )
        return {"updated": True, "enabled": enabled_value}

    async def mcp_start(params: RpcParams) -> JsonValue:
        connector_id = _require_str(params, "connectorId")
        secrets_param = params.get("secrets") or {}
        if not isinstance(secrets_param, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in secrets_param.items()
        ):
            raise RpcError(
                code=INVALID_PARAMS,
                message="secrets must be an object of string→string",
            )
        try:
            installed = await manager.start(connector_id, secrets=secrets_param)
        except ConnectorNotInstalledError as exc:
            raise RpcError(code=INVALID_PARAMS, message=str(exc)) from exc
        except ConnectorNotConfiguredError as exc:
            raise RpcError(code=INVALID_PARAMS, message=str(exc)) from exc
        except McpError as exc:
            raise RpcError(code=INTERNAL_ERROR, message=str(exc)) from exc
        return installed.to_wire()

    async def mcp_stop(params: RpcParams) -> JsonValue:
        connector_id = _require_str(params, "connectorId")
        try:
            stopped = await manager.stop(connector_id, ignore_missing=True)
        except McpError as exc:
            raise RpcError(code=INTERNAL_ERROR, message=str(exc)) from exc
        return {"stopped": stopped}

    async def mcp_list_tools(params: RpcParams) -> JsonValue:
        connector_id = _require_str(params, "connectorId")
        try:
            tools = await manager.list_tools(connector_id)
        except ConnectorNotInstalledError as exc:
            raise RpcError(code=INVALID_PARAMS, message=str(exc)) from exc
        except McpError as exc:
            raise RpcError(code=INTERNAL_ERROR, message=str(exc)) from exc
        return {"tools": tools}

    async def mcp_call_tool(params: RpcParams) -> JsonValue:
        connector_id = _require_str(params, "connectorId")
        tool_name = _require_str(params, "toolName")
        arguments_param: Any = params.get("arguments") or {}
        if not isinstance(arguments_param, dict):
            raise RpcError(code=INVALID_PARAMS, message="arguments must be an object")
        try:
            result = await manager.call_tool(connector_id, tool_name, arguments_param)
        except ToolNotGrantedError as exc:
            raise RpcError(code=INVALID_PARAMS, message=str(exc)) from exc
        except ConnectorNotInstalledError as exc:
            raise RpcError(code=INVALID_PARAMS, message=str(exc)) from exc
        except McpError as exc:
            raise RpcError(code=INTERNAL_ERROR, message=str(exc)) from exc
        return result

    dispatcher.register("mcp.catalog", mcp_catalog)
    dispatcher.register("mcp.list", mcp_list)
    dispatcher.register("mcp.install", mcp_install)
    dispatcher.register("mcp.uninstall", mcp_uninstall)
    dispatcher.register("mcp.set_grants", mcp_set_grants)
    dispatcher.register("mcp.set_enabled", mcp_set_enabled)
    dispatcher.register("mcp.start", mcp_start)
    dispatcher.register("mcp.stop", mcp_stop)
    dispatcher.register("mcp.list_tools", mcp_list_tools)
    dispatcher.register("mcp.call_tool", mcp_call_tool)


def _require_str(params: RpcParams, key: str) -> str:
    value = params.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RpcError(code=INVALID_PARAMS, message=f"missing or non-string {key!r}")
    return value
