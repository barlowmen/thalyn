/**
 * In-memory message shapes for the chat surface. Distinct from the
 * wire-level chunks in `@/lib/chat`: these are rebuilt as chunks
 * arrive and are what the renderer iterates.
 */

export type AssistantSegment =
  | { kind: "text"; text: string }
  | {
      kind: "tool_call";
      callId: string;
      tool: string;
      input: Record<string, unknown>;
      output?: string;
      isError?: boolean;
    }
  | { kind: "error"; message: string; code?: string };

export type Message =
  | {
      id: string;
      role: "user";
      text: string;
    }
  | {
      id: string;
      role: "assistant";
      segments: AssistantSegment[];
      model?: string;
      done: boolean;
      totalCostUsd?: number;
    };
