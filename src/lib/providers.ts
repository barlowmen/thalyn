/**
 * Provider metadata + key-management bindings.
 *
 * Mirrors the wire shape exported by the Rust `provider` module. The
 * Rust side serialises with serde rename_all = "camelCase", so the
 * fields here match exactly.
 */

import { invoke } from "@tauri-apps/api/core";

export type ReliabilityTier = "high" | "medium" | "low" | "unknown";

export type ProviderKind =
  | "anthropic"
  | "openai_compatible"
  | "ollama"
  | "llama_cpp"
  | "mlx";

export type CapabilityProfile = {
  maxContextTokens: number;
  supportsToolUse: boolean;
  toolUseReliability: ReliabilityTier;
  supportsVision: boolean;
  supportsStreaming: boolean;
  local: boolean;
};

export type ProviderMeta = {
  id: string;
  displayName: string;
  kind: ProviderKind;
  defaultModel: string;
  capabilityProfile: CapabilityProfile;
  configured: boolean;
  enabled: boolean;
};

/** List the provider registry as the renderer should display it. */
export function listProviders(): Promise<ProviderMeta[]> {
  return invoke<ProviderMeta[]>("list_providers");
}

/**
 * Save an API key into the OS keychain. Empty / whitespace-only
 * values are rejected by the core.
 */
export function saveApiKey(providerId: string, apiKey: string): Promise<void> {
  return invoke<void>("save_api_key", { providerId, apiKey });
}

/** Remove the keychain entry for a provider. */
export function clearApiKey(providerId: string): Promise<void> {
  return invoke<void>("clear_api_key", { providerId });
}

/** True when the provider has a key on file in the keychain. */
export function isProviderConfigured(providerId: string): Promise<boolean> {
  return invoke<boolean>("provider_configured", { providerId });
}
