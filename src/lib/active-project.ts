/**
 * Persisted "foreground project" choice.
 *
 * v0.31 introduces multi-project juggling (F3.7); the renderer
 * keeps track of which project pill is foregrounded so subsequent
 * turns get biased toward that project. Stored in localStorage and
 * broadcast via a custom event so components elsewhere in the tree
 * (composer, message list, digest) can subscribe without prop
 * drilling. Mirrors the active-provider pattern so future per-
 * project provider routing slots in cleanly.
 *
 * ``DEFAULT_PROJECT_ID`` matches the seeded-by-migration-004
 * project so fresh installs pick a sensible foreground without
 * having to ask the brain on first paint.
 */

import { DEFAULT_PROJECT_ID } from "@/lib/projects";

const STORAGE_KEY = "thalyn:active-project";

export function readActiveProject(): string {
  if (typeof window === "undefined") return DEFAULT_PROJECT_ID;
  try {
    return window.localStorage.getItem(STORAGE_KEY) ?? DEFAULT_PROJECT_ID;
  } catch {
    return DEFAULT_PROJECT_ID;
  }
}

export function writeActiveProject(projectId: string): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STORAGE_KEY, projectId);
  } catch {
    // best-effort
  }
}

export const ACTIVE_PROJECT_EVENT = "thalyn:active-project-changed";

export function emitActiveProjectChange(projectId: string): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(
    new CustomEvent(ACTIVE_PROJECT_EVENT, { detail: projectId }),
  );
}

export function subscribeActiveProject(
  handler: (projectId: string) => void,
): () => void {
  if (typeof window === "undefined") return () => undefined;
  const listener = (event: Event) => {
    const detail = (event as CustomEvent<string>).detail;
    if (typeof detail === "string") handler(detail);
  };
  window.addEventListener(ACTIVE_PROJECT_EVENT, listener);
  return () => window.removeEventListener(ACTIVE_PROJECT_EVENT, listener);
}
