"""First-party MCP connector catalog.

The three connectors here cover the productivity surfaces called
out in ``01-requirements.md`` F4.5-F4.6: Slack for chat-with-humans,
Microsoft Office for documents, Google Calendar for events. Each
descriptor points at a public MCP server so the user can stand it
up themselves; we do not embed credentials, vendor anything, or
proxy traffic on their behalf.

Community connectors will arrive through the same shape via the
marketplace once the install path is exercised on these three.
"""

from __future__ import annotations

from thalyn_brain.mcp.descriptor import (
    ConnectorAuth,
    ConnectorDescriptor,
    ConnectorTool,
)

_SLACK = ConnectorDescriptor(
    connector_id="slack",
    display_name="Slack",
    summary=(
        "Read recent messages, search channels, and post on the user's "
        "behalf via the Slack MCP server."
    ),
    category="chat",
    transport="stdio",
    command="npx",
    args=("-y", "@modelcontextprotocol/server-slack"),
    env_from_secrets={
        "SLACK_BOT_TOKEN": "bot_token",
        "SLACK_TEAM_ID": "team_id",
    },
    required_secrets=(
        ConnectorAuth(
            key="bot_token",
            label="Slack bot token",
            description=(
                "Create a Slack app, install it to your workspace, and paste "
                "the bot user OAuth token (starts with xoxb-)."
            ),
            placeholder="xoxb-...",
        ),
        ConnectorAuth(
            key="team_id",
            label="Workspace ID",
            description="The T-prefixed workspace identifier the bot was installed to.",
            placeholder="T01234ABCDE",
        ),
    ),
    advertised_tools=(
        ConnectorTool("slack_list_channels", "List channels the bot can see."),
        ConnectorTool("slack_get_channel_history", "Read the most recent messages in a channel."),
        ConnectorTool("slack_post_message", "Send a message to a channel.", sensitive=True),
        ConnectorTool("slack_reply_to_thread", "Reply to a thread.", sensitive=True),
    ),
    vendor="modelcontextprotocol",
    homepage="https://github.com/modelcontextprotocol/servers/tree/main/src/slack",
    first_party=True,
)


_OFFICE = ConnectorDescriptor(
    connector_id="office",
    display_name="Microsoft Office",
    summary=(
        "Read Word, Excel, and PowerPoint files in OneDrive / SharePoint via "
        "the Microsoft Office MCP server."
    ),
    category="productivity",
    transport="streamable_http",
    url="https://office-mcp.example.com/mcp",
    header_from_secrets={"Authorization": "access_token"},
    header_template="Bearer {value}",
    required_secrets=(
        ConnectorAuth(
            key="access_token",
            label="Microsoft Graph access token",
            description=(
                "OAuth access token with Files.Read.All scope. The Microsoft "
                "Office MCP server documents how to mint one."
            ),
            placeholder="ey...",
        ),
    ),
    advertised_tools=(
        ConnectorTool("office_list_files", "List recent files across OneDrive."),
        ConnectorTool("office_read_excel", "Extract a worksheet's cells as JSON."),
        ConnectorTool("office_read_word", "Extract a Word document's text."),
        ConnectorTool("office_read_powerpoint", "Extract slide text and notes."),
    ),
    vendor="microsoft",
    homepage="https://learn.microsoft.com/microsoft-365/copilot/extensibility/copilot-apis-overview",
    first_party=True,
)


_CALENDAR = ConnectorDescriptor(
    connector_id="google_calendar",
    display_name="Google Calendar",
    summary=(
        "List upcoming events, free/busy lookups, and event creation via the "
        "Google Calendar MCP server."
    ),
    category="calendar",
    transport="stdio",
    command="npx",
    args=("-y", "@modelcontextprotocol/server-gcal"),
    env_from_secrets={
        "GCAL_CLIENT_ID": "client_id",
        "GCAL_CLIENT_SECRET": "client_secret",
        "GCAL_REFRESH_TOKEN": "refresh_token",
    },
    required_secrets=(
        ConnectorAuth(
            key="client_id",
            label="OAuth client ID",
            description="Google Cloud OAuth 2.0 client ID for a desktop app.",
            placeholder="123-abc.apps.googleusercontent.com",
        ),
        ConnectorAuth(
            key="client_secret",
            label="OAuth client secret",
            description="Paired client secret for the OAuth client above.",
            placeholder="GOCSPX-...",
        ),
        ConnectorAuth(
            key="refresh_token",
            label="Refresh token",
            description=(
                "Long-lived refresh token minted from the OAuth client. The "
                "MCP server will exchange it for short-lived access tokens."
            ),
            placeholder="1//0g...",
        ),
    ),
    advertised_tools=(
        ConnectorTool("gcal_list_events", "List events in a date range."),
        ConnectorTool("gcal_freebusy", "Check free/busy windows across calendars."),
        ConnectorTool("gcal_create_event", "Create a calendar event.", sensitive=True),
        ConnectorTool("gcal_update_event", "Update an existing event.", sensitive=True),
    ),
    vendor="modelcontextprotocol",
    homepage="https://github.com/modelcontextprotocol/servers/tree/main/src/gcal",
    first_party=True,
)


def builtin_catalog() -> list[ConnectorDescriptor]:
    """Return the in-tree first-party connector descriptors.

    Order matches the categories most users will reach for first;
    the marketplace UI re-sorts and filters from there.
    """
    return [_SLACK, _OFFICE, _CALENDAR]


__all__ = ["builtin_catalog"]
