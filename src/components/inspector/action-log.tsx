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

export function ActionLog({ entries }: { entries: ActionLogEntry[] }) {
  if (entries.length === 0) {
    return (
      <p className="text-xs text-muted-foreground">No actions yet.</p>
    );
  }
  return (
    <ul
      className="space-y-1.5 max-h-[40vh] overflow-y-auto"
      role="log"
      aria-live="polite"
      aria-label="Action log"
    >
      {entries.map((entry, idx) => (
        <li
          key={`${entry.atMs}-${idx}`}
          className="rounded-md bg-muted/40 px-2 py-1.5 font-mono text-[11px] leading-relaxed"
        >
          <div className="flex items-baseline gap-2">
            <span className="text-muted-foreground">
              {formatTime(entry.atMs)}
            </span>
            <span className={cn("font-semibold uppercase", KIND_TONE[entry.kind])}>
              {KIND_LABEL[entry.kind]}
            </span>
          </div>
          <p className="mt-0.5 truncate text-xs text-muted-foreground">
            {summarize(entry)}
          </p>
        </li>
      ))}
    </ul>
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
