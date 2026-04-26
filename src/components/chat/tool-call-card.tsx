import { ChevronRight, TerminalSquare } from "lucide-react";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

type Props = {
  callId: string;
  tool: string;
  input: Record<string, unknown>;
  output?: string;
  isError?: boolean;
};

/**
 * Per F11.4 tool calls render as collapsed-by-default cards. The
 * preview line summarises the call; expanding the card reveals the
 * full inputs and the captured output.
 */
export function ToolCallCard({ callId, tool, input, output, isError }: Props) {
  const [expanded, setExpanded] = useState(false);
  const preview = previewFor(tool, input);

  return (
    <div
      className={cn(
        "rounded-md border border-border bg-card",
        "transition-colors",
        isError && "border-destructive/40",
      )}
      data-testid={`tool-call-${callId}`}
    >
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
        className={cn(
          "flex w-full items-center gap-2 px-3 py-2 text-left text-sm",
          "rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        )}
      >
        <ChevronRight
          aria-hidden
          className={cn(
            "h-4 w-4 shrink-0 transition-transform",
            expanded && "rotate-90",
          )}
        />
        <TerminalSquare aria-hidden className="h-4 w-4 shrink-0 text-muted-foreground" />
        <span className="font-mono text-xs">{tool}</span>
        <span className="truncate text-xs text-muted-foreground">
          {preview}
        </span>
        <span className="ml-auto flex items-center gap-2">
          {output === undefined ? (
            <Badge tone="muted">Running…</Badge>
          ) : isError ? (
            <Badge tone="danger">Error</Badge>
          ) : (
            <Badge tone="success">Done</Badge>
          )}
        </span>
      </button>

      {expanded && (
        <div className="space-y-3 border-t border-border px-3 pb-3 pt-2">
          <div className="space-y-1">
            <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
              Input
            </p>
            <pre className="overflow-x-auto rounded bg-muted px-2 py-1.5 font-mono text-xs">
              {JSON.stringify(input, null, 2)}
            </pre>
          </div>
          {output !== undefined && (
            <div className="space-y-1">
              <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
                Output
              </p>
              <pre
                className={cn(
                  "overflow-x-auto rounded px-2 py-1.5 font-mono text-xs",
                  isError
                    ? "bg-destructive/10 text-destructive"
                    : "bg-muted",
                )}
              >
                {output || "(no output)"}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function previewFor(_tool: string, input: Record<string, unknown>): string {
  // Common fields across the tools the brain ships with: command for
  // Bash, file_path for Read/Edit, etc. Show the most-likely-useful
  // single field on the preview row.
  for (const key of ["command", "path", "file_path", "url", "query"]) {
    const value = input[key];
    if (typeof value === "string" && value) {
      return value;
    }
  }
  const keys = Object.keys(input);
  if (keys.length === 0) return "";
  return `{ ${keys.join(", ")} }`;
}
