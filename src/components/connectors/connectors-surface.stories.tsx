import type { Meta, StoryObj } from "@storybook/react-vite";

import { ConnectorsView } from "@/components/connectors/connectors-surface";
import type { ConnectorDescriptor, InstalledConnector } from "@/lib/mcp";

const meta: Meta<typeof ConnectorsView> = {
  title: "Connectors/Surface",
  component: ConnectorsView,
  parameters: { layout: "fullscreen" },
  decorators: [
    (Story) => (
      <div className="h-[720px] w-[920px] bg-background">
        <Story />
      </div>
    ),
  ],
  args: {
    onRefresh: () => undefined,
    onChanged: () => undefined,
  },
};

export default meta;
type Story = StoryObj<typeof ConnectorsView>;

const slack: ConnectorDescriptor = {
  connectorId: "slack",
  displayName: "Slack",
  summary:
    "Read recent messages, search channels, and post on the user's behalf via the Slack MCP server.",
  category: "chat",
  transport: "stdio",
  command: "npx",
  args: ["-y", "@modelcontextprotocol/server-slack"],
  url: null,
  envFromSecrets: { SLACK_BOT_TOKEN: "bot_token", SLACK_TEAM_ID: "team_id" },
  headerFromSecrets: {},
  headerTemplate: "{value}",
  requiredSecrets: [
    {
      key: "bot_token",
      label: "Slack bot token",
      description: "xoxb- token from your Slack app's OAuth & Permissions page.",
      placeholder: "xoxb-...",
      optional: false,
    },
    {
      key: "team_id",
      label: "Workspace ID",
      description: "T-prefixed workspace identifier.",
      placeholder: "T01234ABCDE",
      optional: false,
    },
  ],
  advertisedTools: [
    { name: "slack_list_channels", description: "List channels.", sensitive: false },
    {
      name: "slack_get_channel_history",
      description: "Read recent messages in a channel.",
      sensitive: false,
    },
    {
      name: "slack_post_message",
      description: "Send a message to a channel.",
      sensitive: true,
    },
  ],
  vendor: "modelcontextprotocol",
  homepage:
    "https://github.com/modelcontextprotocol/servers/tree/main/src/slack",
  firstParty: true,
};

const calendar: ConnectorDescriptor = {
  connectorId: "google_calendar",
  displayName: "Google Calendar",
  summary:
    "List upcoming events, free/busy lookups, and event creation via the Google Calendar MCP server.",
  category: "calendar",
  transport: "stdio",
  command: "npx",
  args: ["-y", "@modelcontextprotocol/server-gcal"],
  url: null,
  envFromSecrets: {},
  headerFromSecrets: {},
  headerTemplate: "{value}",
  requiredSecrets: [
    {
      key: "refresh_token",
      label: "Refresh token",
      description: "Long-lived refresh token from your Google Cloud OAuth client.",
      placeholder: "1//0g...",
      optional: false,
    },
  ],
  advertisedTools: [
    { name: "gcal_list_events", description: "List events in a date range.", sensitive: false },
    {
      name: "gcal_create_event",
      description: "Create a calendar event.",
      sensitive: true,
    },
  ],
  vendor: "modelcontextprotocol",
  homepage: null,
  firstParty: true,
};

const installedSlack: InstalledConnector = {
  connectorId: "slack",
  descriptor: slack,
  grantedTools: ["slack_list_channels", "slack_get_channel_history"],
  enabled: true,
  installedAtMs: Date.UTC(2026, 3, 27, 12, 0, 0),
  updatedAtMs: Date.UTC(2026, 3, 27, 13, 0, 0),
  running: true,
  lastError: null,
};

export const Loading: Story = {
  args: {
    catalog: [],
    installed: [],
    loading: true,
    error: null,
  },
};

export const EmptyCatalog: Story = {
  args: {
    catalog: [],
    installed: [],
    loading: false,
    error: null,
  },
};

export const CatalogOnly: Story = {
  args: {
    catalog: [slack, calendar],
    installed: [],
    loading: false,
    error: null,
  },
};

export const SlackInstalledRunning: Story = {
  args: {
    catalog: [slack, calendar],
    installed: [installedSlack],
    loading: false,
    error: null,
  },
};

export const ErrorState: Story = {
  args: {
    catalog: [],
    installed: [],
    loading: false,
    error: "Failed to load connectors: brain sidecar not responding.",
  },
};
