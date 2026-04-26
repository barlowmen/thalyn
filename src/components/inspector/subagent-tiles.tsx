import { Bot, X } from "lucide-react";

import type { SubAgentTile } from "@/components/inspector/use-subagent-tree";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { RunStatus } from "@/lib/runs";

const STATUS_TONE: Record<
  RunStatus,
  "default" | "success" | "warning" | "danger" | "muted"
> = {
  pending: "muted",
  planning: "warning",
  awaiting_approval: "warning",
  running: "default",
  paused: "warning",
  completed: "success",
  errored: "danger",
  killed: "danger",
};

const STATUS_LABEL: Record<RunStatus, string> = {
  pending: "Pending",
  planning: "Planning",
  awaiting_approval: "Awaiting approval",
  running: "Running",
  paused: "Paused",
  completed: "Completed",
  errored: "Errored",
  killed: "Killed",
};

const TERMINAL_STATUSES: RunStatus[] = ["completed", "errored", "killed"];

type Props = {
  tiles: SubAgentTile[];
  activeRunId?: string | null;
  onOpen?: (runId: string) => void;
  onKill?: (runId: string) => void;
};

/**
 * Per-sub-agent tile list — rendered in the inspector pane and as a
 * compact strip above the chat composer. Each tile is keyboard-
 * focusable and exposes status, title, and (when running) a kill
 * control.
 */
export function SubAgentTiles({
  tiles,
  activeRunId,
  onOpen,
  onKill,
}: Props) {
  if (tiles.length === 0) return null;
  return (
    <ul className="space-y-2" aria-label="Sub-agents">
      {tiles.map((tile) => {
        const isActive = activeRunId === tile.runId;
        const isTerminal = TERMINAL_STATUSES.includes(tile.status);
        return (
          <li key={tile.runId}>
            <div
              className={`group flex items-center gap-2 rounded-md border border-border bg-bg px-3 py-2 transition-colors ${
                isActive ? "border-accent" : "hover:border-muted-foreground"
              }`}
            >
              <button
                type="button"
                onClick={() => onOpen?.(tile.runId)}
                className="flex flex-1 items-center gap-2 text-left"
              >
                <Bot className="h-4 w-4 text-muted-foreground" aria-hidden />
                <div className="flex-1 overflow-hidden">
                  <p className="truncate text-sm">{tile.title}</p>
                  <p className="truncate font-mono text-[10px] text-muted-foreground">
                    {tile.runId}
                  </p>
                </div>
              </button>
              <Badge tone={STATUS_TONE[tile.status]}>
                {STATUS_LABEL[tile.status]}
              </Badge>
              {!isTerminal && onKill && (
                <Button
                  size="sm"
                  variant="ghost"
                  aria-label={`Kill sub-agent ${tile.title}`}
                  onClick={() => onKill(tile.runId)}
                >
                  <X className="h-3.5 w-3.5" aria-hidden />
                </Button>
              )}
            </div>
          </li>
        );
      })}
    </ul>
  );
}
