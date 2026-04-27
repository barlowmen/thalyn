import { invoke } from "@tauri-apps/api/core";

export type ConnectorAuth = {
  key: string;
  label: string;
  description: string;
  placeholder: string;
  optional: boolean;
};

export type ConnectorTool = {
  name: string;
  description: string;
  sensitive: boolean;
};

export type ConnectorDescriptor = {
  connectorId: string;
  displayName: string;
  summary: string;
  category: "chat" | "productivity" | "calendar" | "dev" | "other";
  transport: "stdio" | "streamable_http";
  command: string | null;
  args: string[];
  url: string | null;
  envFromSecrets: Record<string, string>;
  headerFromSecrets: Record<string, string>;
  headerTemplate: string;
  requiredSecrets: ConnectorAuth[];
  advertisedTools: ConnectorTool[];
  vendor: string;
  homepage: string | null;
  firstParty: boolean;
};

export type LiveTool = {
  name: string;
  description: string;
  inputSchema: unknown;
};

export type InstalledConnector = {
  connectorId: string;
  descriptor: ConnectorDescriptor;
  grantedTools: string[];
  enabled: boolean;
  installedAtMs: number;
  updatedAtMs: number;
  running: boolean;
  lastError: string | null;
  liveTools?: LiveTool[];
};

export type CatalogResponse = {
  connectors: ConnectorDescriptor[];
};

export type InstalledResponse = {
  installed: InstalledConnector[];
};

export type ToolCallContent =
  | { type: "text"; text: string }
  | { type: "image"; data: string; mimeType: string }
  | { type: string; raw?: string };

export type ToolCallResult = {
  content: ToolCallContent[];
  isError: boolean;
};

export async function getCatalog(): Promise<CatalogResponse> {
  return await invoke<CatalogResponse>("mcp_catalog");
}

export async function getInstalled(): Promise<InstalledResponse> {
  return await invoke<InstalledResponse>("mcp_list");
}

export async function installConnector(
  connectorId: string,
  grantedTools?: string[],
): Promise<InstalledConnector> {
  return await invoke<InstalledConnector>("mcp_install", {
    connectorId,
    grantedTools,
  });
}

export async function uninstallConnector(connectorId: string): Promise<void> {
  await invoke("mcp_uninstall", { connectorId });
}

export async function setGrants(
  connectorId: string,
  grantedTools: string[],
): Promise<void> {
  await invoke("mcp_set_grants", { connectorId, grantedTools });
}

export async function setEnabled(
  connectorId: string,
  enabled: boolean,
): Promise<void> {
  await invoke("mcp_set_enabled", { connectorId, enabled });
}

export async function saveSecret(
  connectorId: string,
  secretKey: string,
  value: string,
): Promise<void> {
  await invoke("mcp_save_secret", { connectorId, secretKey, value });
}

export async function clearSecret(
  connectorId: string,
  secretKey: string,
): Promise<void> {
  await invoke("mcp_clear_secret", { connectorId, secretKey });
}

export async function getSecretStatus(
  connectorId: string,
  secretKeys: string[],
): Promise<Record<string, boolean>> {
  return await invoke<Record<string, boolean>>("mcp_secret_status", {
    connectorId,
    secretKeys,
  });
}

export async function startConnector(
  connectorId: string,
  secretKeys: string[],
): Promise<InstalledConnector> {
  return await invoke<InstalledConnector>("mcp_start", {
    connectorId,
    secretKeys,
  });
}

export async function stopConnector(connectorId: string): Promise<void> {
  await invoke("mcp_stop", { connectorId });
}

export async function callTool(
  connectorId: string,
  toolName: string,
  args: Record<string, unknown> = {},
): Promise<ToolCallResult> {
  return await invoke<ToolCallResult>("mcp_call_tool", {
    connectorId,
    toolName,
    arguments: args,
  });
}
