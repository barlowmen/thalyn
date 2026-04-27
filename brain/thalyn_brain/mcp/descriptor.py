"""Connector descriptors — the static shape of an MCP connector.

A descriptor is the contract the renderer reads when populating the
marketplace UI: what the connector is, how it's reached over MCP,
which secrets the user must paste before it can run, and which
tools it exposes by default. The set of descriptors shipped in-tree
lives in :mod:`thalyn_brain.mcp.catalog`; community connectors will
arrive through the same shape once the marketplace exists.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ConnectorTransport = Literal["stdio", "streamable_http"]
ConnectorCategory = Literal["chat", "productivity", "calendar", "dev", "other"]


@dataclass(frozen=True)
class ConnectorAuth:
    """Per-connector secret slot the user must populate.

    ``key`` is appended to the connector id to form the keychain
    namespace (`mcp:<connector_id>:<key>`); ``label`` and
    ``description`` are surfaced verbatim by the install UI;
    ``placeholder`` hints at the expected shape (e.g.
    ``"xoxb-..."``); ``optional`` allows connectors with mixed
    auth (some tools work without it).
    """

    key: str
    label: str
    description: str
    placeholder: str = ""
    optional: bool = False

    def to_wire(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "description": self.description,
            "placeholder": self.placeholder,
            "optional": self.optional,
        }


@dataclass(frozen=True)
class ConnectorTool:
    """Static metadata about a tool a connector exposes.

    Used by the catalog to advertise what a connector *can do*
    before it has been installed and queried live. Once the
    connector is installed, the live `list_tools` response
    supersedes this — the static set is just for marketplace UX.
    """

    name: str
    description: str
    sensitive: bool = False

    def to_wire(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "sensitive": self.sensitive,
        }


@dataclass(frozen=True)
class ConnectorDescriptor:
    """Full description of one MCP connector the user can install."""

    connector_id: str
    display_name: str
    summary: str
    category: ConnectorCategory
    transport: ConnectorTransport
    # For stdio transports: the command + args to spawn.
    command: str | None = None
    args: tuple[str, ...] = ()
    # For streamable-http transports: the base URL.
    url: str | None = None
    # Environment variables the spawned MCP server expects, mapped
    # from secret keys (e.g. ``"SLACK_BOT_TOKEN": "bot_token"``).
    # Only used by stdio transports; HTTP connectors carry auth in
    # headers handled by the connector itself.
    env_from_secrets: dict[str, str] = field(default_factory=dict)
    # Headers for streamable-http transports, also keyed by secret
    # key (e.g. ``"Authorization": "api_key"`` → header value comes
    # from the secret named ``api_key``, formatted by ``header_template``).
    header_from_secrets: dict[str, str] = field(default_factory=dict)
    header_template: str = "{value}"
    # Required secret slots — render these in the install dialog.
    required_secrets: tuple[ConnectorAuth, ...] = ()
    # Static catalog of tools advertised in the marketplace.
    advertised_tools: tuple[ConnectorTool, ...] = ()
    # Vendor of the MCP server (the people who ship it, not us).
    vendor: str = "third-party"
    # Where the user can read about the underlying MCP server.
    homepage: str | None = None
    # Marks first-party (vendored) connectors so the UI can flag
    # them apart from community installs once the marketplace
    # accepts external descriptors.
    first_party: bool = False

    def to_wire(self) -> dict[str, Any]:
        return {
            "connectorId": self.connector_id,
            "displayName": self.display_name,
            "summary": self.summary,
            "category": self.category,
            "transport": self.transport,
            "command": self.command,
            "args": list(self.args),
            "url": self.url,
            "envFromSecrets": dict(self.env_from_secrets),
            "headerFromSecrets": dict(self.header_from_secrets),
            "headerTemplate": self.header_template,
            "requiredSecrets": [s.to_wire() for s in self.required_secrets],
            "advertisedTools": [t.to_wire() for t in self.advertised_tools],
            "vendor": self.vendor,
            "homepage": self.homepage,
            "firstParty": self.first_party,
        }


__all__ = [
    "ConnectorAuth",
    "ConnectorCategory",
    "ConnectorDescriptor",
    "ConnectorTool",
    "ConnectorTransport",
]
