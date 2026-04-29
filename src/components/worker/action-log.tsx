import { ChevronDown, ChevronRight } from "lucide-react";
import { useState } from "react";

import type { ActionLogEntry } from "@/lib/runs";
import { cn } from "@/lib/utils";

const KIND_LABEL: Record<ActionLogEntry["kind"], string> = {
  tool_call: "Tool",
  llm_call: "LLM",
  decision: "Decision",
  file_change: "File",
  approval: "Approval",
  drift_check: "Drift",
  node_transition: "Step",
};

const KIND_TONE: Record<ActionLogEntry["kind"], string> = {
  tool_call: "text-foreground",
  llm_call: "text-foreground",
  decision: "text-muted-foreground",
  file_change: "text-foreground",
  approval: "text-warning",
  drift_check: "text-warning",
  node_transition: "text-muted-foreground",
};

/**
 * Append-only action log with collapsible entries (F2.7). Wraps the
 * inner list in ``role="log"`` + ``aria-live="polite"`` so screen
 * readers announce new entries as the brain streams them.
 */
export function ActionLog({ entries }: { entries: readonly ActionLogEntry[] }) {
  if (entries.length === 0) {
    return (
      <p className="text-xs text-muted-foreground">No actions yet.</p>
    );
  }
  return (
    // Wrap the live region as a ``<div role="log">`` rather than putting
    // the role on the ``<ul>`` directly — axe rejects ``role="log"`` on a
    // list because the override drops the implicit list semantics, which
    // then orphans the ``<li>`` children. The wrapping ``<div>`` keeps
    // both the live-region announcement and the list semantics intact.
    <div
      role="log"
      aria-live="polite"
      aria-label="Action log"
      className="max-h-[40vh] overflow-y-auto"
    >
      <ul className="space-y-1">
        {entries.map((entry, idx) => (
          <ActionRow key={`${entry.atMs}-${idx}`} entry={entry} />
        ))}
      </ul>
    </div>
  );
}

function ActionRow({ entry }: { entry: ActionLogEntry }) {
  const [expanded, setExpanded] = useState(false);
  const summary = summarize(entry);
  const detail = detailFor(entry);
  return (
    <li className="rounded-md bg-muted/40 px-2 py-1 font-mono text-[11px] leading-relaxed">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-start gap-1.5 text-left"
        aria-expanded={expanded}
      >
        {detail ? (
          expanded ? (
            <ChevronDown className="mt-0.5 h-3 w-3 shrink-0 text-muted-foreground" aria-hidden />
          ) : (
            <ChevronRight className="mt-0.5 h-3 w-3 shrink-0 text-muted-foreground" aria-hidden />
          )
        ) : (
          <span className="mt-0.5 inline-block h-3 w-3 shrink-0" aria-hidden />
        )}
        <span className="text-muted-foreground">{formatTime(entry.atMs)}</span>
        <span className={cn("font-semibold uppercase", KIND_TONE[entry.kind])}>
          {KIND_LABEL[entry.kind]}
        </span>
        <span className="flex-1 truncate text-muted-foreground">
          {summary}
        </span>
      </button>
      {expanded && detail && (
        <pre className="mt-1 max-h-40 overflow-auto rounded bg-background/40 p-1.5 text-[10px]">
          {detail}
        </pre>
      )}
    </li>
  );
}

function formatTime(ms: number): string {
  const date = new Date(ms);
  return date.toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function summarize(entry: ActionLogEntry): string {
  const p = entry.payload as Record<string, unknown>;
  switch (entry.kind) {
    case "tool_call": {
      const tool = typeof p.tool === "string" ? p.tool : "tool";
      const callId = typeof p.callId === "string" ? p.callId : "";
      const result = "result" in p;
      return result
        ? `${tool} → result (${callId})`
        : `${tool}(${callId})`;
    }
    case "node_transition":
      return `${p.from ?? "?"} → ${p.to ?? "?"}`;
    case "decision":
      return typeof p.step === "string" ? `step: ${p.step}` : "decision";
    default:
      return JSON.stringify(p).slice(0, 100);
  }
}

function detailFor(entry: ActionLogEntry): string | null {
  try {
    const text = JSON.stringify(entry.payload, null, 2);
    return text.length > 80 ? text : null;
  } catch {
    return null;
  }
}
