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

/**
 * One F12.7 / ADR-0027 audit verdict in wire shape. Mirrors
 * ``InfoFlowAuditReport.to_wire()`` on the brain side. ``sourceRef``
 * and ``outputRef`` are provenance pointers the renderer's drill-
 * into-source UX (F1.10) follows back to the underlying record.
 */
export type InfoFlowAuditWire = {
  mode: "plan_vs_action" | "reported_vs_truth" | "relayed_vs_source";
  driftScore: number;
  confidence: "low" | "medium" | "high";
  summary: string;
  sourceRef: Record<string, string | number>;
  outputRef: Record<string, string | number>;
  heuristicScore: number;
  llmScore?: number;
};

/**
 * Confidence payload that lands on a delegated assistant turn.
 * ``level`` is the worst across all audits (the renderer reads it to
 * choose the pill tone); ``audit`` is the audit driving that level
 * (the pill's tooltip + drill-into-source target); ``audits`` carries
 * every audit so the user can navigate to each underlying source.
 */
export type ConfidencePayload = {
  level: "low" | "medium" | "high";
  audit: InfoFlowAuditWire;
  audits: InfoFlowAuditWire[];
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
      /**
       * Confidence on a delegated relay (ADR-0027). Present when the
       * brain relayed a lead's reply; absent for the brain's own
       * direct replies (no source to audit against).
       */
      confidence?: ConfidencePayload;
      atMs?: number;
      projectId?: string;
    };
