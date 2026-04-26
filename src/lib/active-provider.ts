/**
 * Persisted "active provider" choice.
 *
 * v0.3 only enables Anthropic; we still treat the selection as
 * first-class so the storage key is in place when the placeholders
 * graduate. Stored in localStorage; per-project storage lands when
 * projects are addressable.
 */

const STORAGE_KEY = "thalyn:active-provider";
const DEFAULT_PROVIDER_ID = "anthropic";

export function readActiveProvider(): string {
  if (typeof window === "undefined") return DEFAULT_PROVIDER_ID;
  try {
    return window.localStorage.getItem(STORAGE_KEY) ?? DEFAULT_PROVIDER_ID;
  } catch {
    return DEFAULT_PROVIDER_ID;
  }
}

export function writeActiveProvider(providerId: string): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STORAGE_KEY, providerId);
  } catch {
    // best-effort
  }
}

export const ACTIVE_PROVIDER_EVENT = "thalyn:active-provider-changed";

export function emitActiveProviderChange(providerId: string): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(
    new CustomEvent(ACTIVE_PROVIDER_EVENT, { detail: providerId }),
  );
}

export function subscribeActiveProvider(
  handler: (providerId: string) => void,
): () => void {
  if (typeof window === "undefined") return () => undefined;
  const listener = (event: Event) => {
    const detail = (event as CustomEvent<string>).detail;
    if (typeof detail === "string") handler(detail);
  };
  window.addEventListener(ACTIVE_PROVIDER_EVENT, listener);
  return () => window.removeEventListener(ACTIVE_PROVIDER_EVENT, listener);
}
