import { invoke } from "@tauri-apps/api/core";

/** Mirror of `observability_status` Tauri command shape. */
export type ObservabilityStatus = {
  sentryDsnConfigured: boolean;
  otelOtlpEndpointConfigured: boolean;
};

/** The two slots the settings panel surfaces. */
export type ObservabilitySecretName = "sentry_dsn" | "otel_otlp_endpoint";

export async function getObservabilityStatus(): Promise<ObservabilityStatus> {
  return await invoke<ObservabilityStatus>("observability_status");
}

export async function saveObservabilitySecret(
  name: ObservabilitySecretName,
  value: string,
): Promise<void> {
  await invoke("save_observability_secret", { name, value });
}

export async function clearObservabilitySecret(
  name: ObservabilitySecretName,
): Promise<void> {
  await invoke("clear_observability_secret", { name });
}
