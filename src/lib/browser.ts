import { invoke } from "@tauri-apps/api/core";

/**
 * Wire-typed mirror of `crate::cef::HostState`. The Rust core picks
 * the variant based on the bundled-CEF host's lifecycle; the
 * renderer's panel reads `kind` and switches on it.
 */
export type BrowserState =
  | { kind: "idle" }
  | { kind: "starting"; profile_dir: string }
  | {
      kind: "running";
      ws_url: string;
      profile_dir: string;
      sdk_version: string;
    }
  | { kind: "exited"; reason: string };

/** Spawn the bundled-CEF child binary and attach the brain. */
export async function startBrowser(): Promise<BrowserState> {
  return await invoke<BrowserState>("browser_start");
}

/** Detach the brain and stop the bundled-CEF child. */
export async function stopBrowser(): Promise<void> {
  await invoke("browser_stop");
}

/** Read the current state without changing it. */
export async function getBrowserStatus(): Promise<BrowserState> {
  return await invoke<BrowserState>("browser_status");
}

/**
 * Friendly label for the active state. Used in the panel header and
 * the activity-rail badge so the user sees a single word at a
 * glance.
 */
export function browserStateLabel(state: BrowserState): string {
  switch (state.kind) {
    case "idle":
      return "Idle";
    case "starting":
      return "Starting…";
    case "running":
      return "Running";
    case "exited":
      return "Exited";
  }
}
