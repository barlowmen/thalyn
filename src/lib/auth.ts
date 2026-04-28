/**
 * Auth-backend bindings.
 *
 * Wraps the brain's ``auth.list / auth.probe / auth.set`` IPC methods
 * via Tauri commands ``auth_list / auth_probe / auth_set``. The wire
 * shape mirrors the Python/Rust serde rendering (camelCase keys,
 * snake_case enum values).
 */

import { invoke } from "@tauri-apps/api/core";

export type AuthBackendKind =
  | "claude_subscription"
  | "anthropic_api"
  | "openai_compat"
  | "ollama"
  | "llama_cpp"
  | "mlx";

export type AuthProbeResult = {
  detected: boolean;
  authenticated: boolean;
  detail: string | null;
  error: string | null;
};

export type AuthBackendDescriptor = {
  kind: AuthBackendKind;
  displayName: string;
  description: string;
  active: boolean;
  probe: AuthProbeResult;
};

export type AuthListResult = {
  activeKind: AuthBackendKind;
  backends: AuthBackendDescriptor[];
};

export type AuthProbeOutcome = {
  kind: AuthBackendKind;
  probe: AuthProbeResult;
};

export type AuthSetResult = {
  activeKind: AuthBackendKind;
  probe: AuthProbeResult;
};

/** Enumerate every auth backend with its current probe state. */
export function listAuthBackends(): Promise<AuthListResult> {
  return invoke<AuthListResult>("auth_list");
}

/** Re-run the probe for a single auth backend. */
export function probeAuthBackend(kind: AuthBackendKind): Promise<AuthProbeOutcome> {
  return invoke<AuthProbeOutcome>("auth_probe", { kind });
}

/** Mark one auth backend as the active brain credential. */
export function setActiveAuthBackend(kind: AuthBackendKind): Promise<AuthSetResult> {
  return invoke<AuthSetResult>("auth_set", { kind });
}
