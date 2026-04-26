import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";

/**
 * Frame the brain emits per pty-output chunk. `seq` is monotonic
 * per session so a renderer can drop late-arriving duplicates.
 */
export type TerminalDataEvent = {
  sessionId: string;
  seq: number;
  data: string;
};

export type TerminalOpenResult = {
  sessionId: string;
  snapshot: string;
};

export async function openTerminal(options: {
  cwd?: string;
  cols?: number;
  rows?: number;
  program?: string;
} = {}): Promise<TerminalOpenResult> {
  return await invoke<TerminalOpenResult>("terminal_open", options);
}

export async function writeTerminal(
  sessionId: string,
  data: string,
): Promise<void> {
  await invoke("terminal_input", { sessionId, data });
}

export async function resizeTerminal(
  sessionId: string,
  cols: number,
  rows: number,
): Promise<void> {
  await invoke("terminal_resize", { sessionId, cols, rows });
}

export async function closeTerminal(sessionId: string): Promise<void> {
  await invoke("terminal_close", { sessionId });
}

export async function listTerminals(): Promise<string[]> {
  const result = await invoke<{ sessions: string[] }>("terminal_list");
  return result.sessions;
}

export async function subscribeTerminal(
  sessionId: string,
  handler: (event: TerminalDataEvent) => void,
): Promise<UnlistenFn> {
  return await listen<TerminalDataEvent>("terminal:data", (event) => {
    if (event.payload.sessionId === sessionId) handler(event.payload);
  });
}
