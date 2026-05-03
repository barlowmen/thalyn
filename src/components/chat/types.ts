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

export type LeadAttribution = {
  /** The agent_id of the lead the brain delegated to. */
  agentId: string;
  /** The lead's display_name at delegation time. Renderer uses this
   *  for the "via Lead-X" chip; later phases plug a live store-lookup
   *  in so renames flow through automatically. */
  displayName?: string;
};

export type Message =
  | {
      id: string;
      role: "user";
      text: string;
      /** Wall-clock ms the message was created. Used by the chat
       *  surface to insert a day-divider when consecutive messages
       *  cross a calendar-day boundary. Optional so the legacy
       *  Storybook fixtures (which don't care about dividers) keep
       *  working unchanged. */
      atMs?: number;
      /** The project this turn was tagged to (foreground bias at
       *  send time, or whatever the brain's classifier resolved to
       *  for ``thread.send``). The renderer surfaces a project pill
       *  above the bubble when set. */
      projectId?: string;
    }
  | {
      id: string;
      role: "assistant";
      segments: AssistantSegment[];
      model?: string;
      done: boolean;
      totalCostUsd?: number;
      leadAttribution?: LeadAttribution;
      atMs?: number;
      projectId?: string;
    };
