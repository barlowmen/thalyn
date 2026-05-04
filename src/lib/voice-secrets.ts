/**
 * Voice secrets — thin wrappers over the Tauri commands that store
 * voice-related credentials in the OS keychain (ADR-0012). Today the
 * only entry is the Deepgram Nova-3 API key (cloud STT opt-in,
 * ADR-0025); future cloud providers add their own ``VoiceSecretName``
 * variant without changing the wire shape this module exposes.
 *
 * Mirrors the observability-secret module's pattern — paste-and-go
 * fields in the settings dialog, no plaintext ever rendered after
 * save.
 */

import { invoke } from "@tauri-apps/api/core";

export type VoiceSecretName = "deepgram_api_key";

export type VoiceSecretStatus = {
  deepgramConfigured: boolean;
};

export async function saveVoiceSecret(
  name: VoiceSecretName,
  value: string,
): Promise<void> {
  await invoke("save_voice_secret", { name, value });
}

export async function clearVoiceSecret(name: VoiceSecretName): Promise<void> {
  await invoke("clear_voice_secret", { name });
}

export async function getVoiceSecretStatus(): Promise<VoiceSecretStatus> {
  return invoke<VoiceSecretStatus>("voice_secret_status");
}
