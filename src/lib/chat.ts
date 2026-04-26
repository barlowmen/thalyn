/**
 * Chat session types + Tauri bindings.
 *
 * The wire shapes mirror `brain/thalyn_brain/provider/base.py` —
 * camelCase keys for everything that crosses the IPC boundary.
 */

import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";

export type ChatChunk =
  | { kind: "start"; model: string }
  | { kind: "text"; delta: string }
  | {
      kind: "tool_call";
      callId: string;
      tool: string;
      input: Record<string, unknown>;
    }
  | {
      kind: "tool_result";
      callId: string;
      output: string;
      isError: boolean;
    }
  | { kind: "stop"; reason: string; totalCostUsd?: number }
  | { kind: "error"; message: string; code?: string };

export type ChatChunkEvent = {
  sessionId: string;
  chunk: ChatChunk;
};

export type ChatSummary = {
  sessionId: string;
  chunks: number;
  reason: string;
  totalCostUsd?: number;
};

export type SendChatParams = {
  sessionId: string;
  providerId: string;
  prompt: string;
  systemPrompt?: string;
};

export function sendChat(params: SendChatParams): Promise<ChatSummary> {
  return invoke<ChatSummary>("send_chat", params);
}

/**
 * Subscribe to streamed chat chunks. The returned function detaches
 * the listener — call it from a React effect's cleanup.
 */
export function subscribeChatChunks(
  handler: (event: ChatChunkEvent) => void,
): Promise<UnlistenFn> {
  return listen<ChatChunkEvent>("chat:chunk", (e) => handler(e.payload));
}
