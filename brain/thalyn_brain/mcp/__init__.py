"""MCP client + connector registry.

The renderer-facing surface (`thalyn_brain.mcp_rpc`) wires this
module into JSON-RPC. Other brain code (the orchestrator's tool
adapter, the runs index) consumes it through :class:`McpManager`.

Public surface intentionally narrow — descriptors describe a
connector's transport, the catalog enumerates the first-party set
shipped in-tree, the registry persists the user's installed and
configured connectors, and the manager owns live sessions.
"""

from thalyn_brain.mcp.catalog import builtin_catalog
from thalyn_brain.mcp.descriptor import (
    ConnectorAuth,
    ConnectorDescriptor,
    ConnectorTool,
    ConnectorTransport,
)
from thalyn_brain.mcp.manager import (
    ConnectorAlreadyInstalledError,
    ConnectorNotConfiguredError,
    ConnectorNotInstalledError,
    InstalledConnector,
    McpError,
    McpManager,
    ToolNotGrantedError,
    UnknownConnectorError,
)
from thalyn_brain.mcp.registry import ConnectorRecord, ConnectorRegistry

__all__ = [
    "ConnectorAlreadyInstalledError",
    "ConnectorAuth",
    "ConnectorDescriptor",
    "ConnectorNotConfiguredError",
    "ConnectorNotInstalledError",
    "ConnectorRecord",
    "ConnectorRegistry",
    "ConnectorTool",
    "ConnectorTransport",
    "InstalledConnector",
    "McpError",
    "McpManager",
    "ToolNotGrantedError",
    "UnknownConnectorError",
    "builtin_catalog",
]
