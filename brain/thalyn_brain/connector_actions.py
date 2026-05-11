"""Connector-setup actions for the action registry.

"Thalyn, set up Slack" is the canonical phrasing. The matcher
resolves the connector by display-name (case-insensitive) or by
connector id; the executor registers the install record and emits a
followup payload that opens the in-app browser drawer at the
connector's homepage so the user can mint their OAuth tokens
without leaving the app (per F5.1 / F9.5).

The install itself is registry-only — the brain doesn't start the
MCP session here. Once the user pastes the required secrets through
the install dialog (kept in the renderer, populated from the
descriptor's ``required_secrets``), the existing ``mcp.start`` path
brings the connector live.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from thalyn_brain.action_registry import (
    Action,
    ActionInput,
    ActionMatch,
    ActionRegistry,
    ActionResult,
)
from thalyn_brain.mcp import (
    ConnectorAlreadyInstalledError,
    McpManager,
    UnknownConnectorError,
)

CONNECTOR_SETUP_ACTION = "connector.setup"

# Sentence-leading "set up X", "install X", "connect X", "wire up X".
# Captures the trailing phrase as ``target``; the executor resolves
# it against the catalog (display name and connector id).
_SETUP = re.compile(
    r"""
    ^\s*
    (?:thalyn[,:\s]+)?
    (?:please\s+)?
    (?:set\s+up|install|connect|wire\s+up|hook\s+up)
    [,:\s]+
    (?P<target>.+?)
    \s*[.!?]?\s*$
    """,
    re.IGNORECASE | re.VERBOSE | re.DOTALL,
)


def _resolve_connector_id(target: str, manager: McpManager) -> str | None:
    """Match a free-form name against the connector catalog.

    Tries display-name first (case-insensitive), then connector id,
    then a prefix fall-back so "Microsoft" resolves to
    ``Microsoft Office``. Returns ``None`` when nothing fits.
    """

    cleaned = target.strip().lower().rstrip("s")  # tolerate "Slack" / "slacks"
    for descriptor in manager.catalog():
        if descriptor.display_name.lower() == cleaned:
            return descriptor.connector_id
        if descriptor.connector_id.lower() == cleaned:
            return descriptor.connector_id
    for descriptor in manager.catalog():
        if descriptor.display_name.lower().startswith(cleaned):
            return descriptor.connector_id
    return None


class ConnectorSetupMatcher:
    """Matches "set up <connector>" / "install <connector>" phrasings."""

    def __init__(self, manager: McpManager) -> None:
        self._manager = manager

    def try_match(
        self,
        prompt: str,
        *,
        context: Mapping[str, Any],
    ) -> ActionMatch | None:
        match = _SETUP.match(prompt.strip())
        if match is None:
            return None
        target = match.group("target").strip()
        # Strip a trailing "for me" / "please" so the resolver sees
        # the bare name.
        target = re.sub(r"\b(?:for\s+me|please)\b\s*$", "", target, flags=re.IGNORECASE).strip()
        if not target:
            return None
        connector_id = _resolve_connector_id(target, self._manager)
        if connector_id is None:
            # The matcher only fires when we know the target — an
            # unknown name falls back to the regular reply flow so
            # Thalyn can offer a help.
            return None
        descriptor = self._manager.descriptor(connector_id)
        return ActionMatch(
            action_name=CONNECTOR_SETUP_ACTION,
            inputs={"connector_id": connector_id},
            preview=f"Set up the {descriptor.display_name} connector",
        )


def register_connector_actions(
    registry: ActionRegistry,
    *,
    manager: McpManager,
) -> None:
    """Register the connector-setup action + matcher on ``registry``."""

    async def setup(inputs: Mapping[str, Any]) -> ActionResult:
        connector_id = str(inputs["connector_id"])
        try:
            descriptor = manager.descriptor(connector_id)
        except UnknownConnectorError as exc:
            return ActionResult(confirmation=f"I don't know that connector — {exc}.")

        already_installed = False
        try:
            await manager.install(connector_id)
        except ConnectorAlreadyInstalledError:
            already_installed = True

        secret_labels = [auth.label for auth in descriptor.required_secrets]
        if already_installed:
            confirmation = (
                f"{descriptor.display_name} is already installed. "
                "I'll open the install dialog so you can refresh credentials if "
                "you need to."
            )
        elif secret_labels:
            joined = ", ".join(secret_labels)
            confirmation = (
                f"Installed {descriptor.display_name}. I'm opening the "
                f"connector's homepage so you can mint the required secrets "
                f"({joined}); paste them back here when you have them."
            )
        else:
            confirmation = (
                f"Installed {descriptor.display_name}. No secrets required — "
                "I'll start it on the next worker run that needs it."
            )

        followup: dict[str, Any] = {
            "connectorId": connector_id,
            "displayName": descriptor.display_name,
            "alreadyInstalled": already_installed,
            "homepage": descriptor.homepage,
            "requiredSecrets": [auth.to_wire() for auth in descriptor.required_secrets],
        }
        return ActionResult(confirmation=confirmation, followup=followup)

    registry.register(
        Action(
            name=CONNECTOR_SETUP_ACTION,
            description=(
                "Install and walk the user through configuring an MCP connector "
                "(e.g. 'set up Slack', 'install Google Calendar'). The renderer "
                "opens the connector's homepage in the in-app browser drawer "
                "and surfaces the required secrets inline."
            ),
            inputs=(
                ActionInput(
                    name="connector_id",
                    description="Which connector to install (resolved from the catalog).",
                    kind="connector_id",
                ),
            ),
            executor=setup,
        )
    )
    registry.register_matcher(ConnectorSetupMatcher(manager))


__all__ = [
    "CONNECTOR_SETUP_ACTION",
    "ConnectorSetupMatcher",
    "register_connector_actions",
]
