import { invoke } from "@tauri-apps/api/core";

import { readActiveProvider } from "@/lib/active-provider";

/**
 * One ghost-text suggestion fetched from the brain.
 *
 * `providerId` is the provider that was actually used (the active
 * one when the call was made). `requestId` lets the renderer drop
 * stale results when the user types past the request that generated
 * them.
 */
export type InlineSuggestion = {
  suggestion: string;
  requestId: string;
  requestedAtMs: number;
  completedAtMs: number;
  providerId: string;
  truncated: boolean;
};

export async function fetchInlineSuggestion(options: {
  prefix: string;
  suffix?: string;
  language?: string;
  requestId?: string;
  providerId?: string;
}): Promise<InlineSuggestion> {
  const providerId = options.providerId ?? readActiveProvider();
  return await invoke<InlineSuggestion>("inline_suggest", {
    providerId,
    prefix: options.prefix,
    suffix: options.suffix ?? "",
    language: options.language ?? "",
    requestId: options.requestId,
  });
}
