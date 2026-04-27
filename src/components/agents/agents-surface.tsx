import { Bot, Compass, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { BudgetMeter } from "@/components/inspector/budget-meter";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  type RunHeader,
  type RunStatus,
  killRun,
  listRuns,
  subscribeRunStatus,
} from "@/lib/runs";
import { cn } from "@/lib/utils";

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

const ACTIVE_STATUSES: RunStatus[] = [
  "pending",
  "planning",
  "awaiting_approval",
  "running",
  "paused",
];

const TERMINAL_STATUSES: RunStatus[] = ["completed", "errored", "killed"];

/**
 * Sub-agent inventory — every run that has a parent (i.e. was
 * spawned by another agent). Sectioned by lifecycle so the eye
 * lands on running first, awaiting-approval second, recent
 * terminal runs last. Click a row to open the existing
 * sub-agent detail surface.
 */
export function AgentsSurface({
  onOpen,
}: {
  onOpen?: (runId: string) => void;
}) {
  const [runs, setRuns] = useState<RunHeader[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const result = await listRuns({ limit: 200 });
      setRuns(result.runs);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setRuns([]);
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Live status updates — flip a row's badge in place when the
  // brain emits a transition for that runId. Avoids needing a
  // full refresh just because something completed.
  useEffect(() => {
    let unlisten: (() => void) | null = null;
    void subscribeRunStatus((event) => {
      setRuns((current) =>
        current
          ? current.map((row) =>
              row.runId === event.runId
                ? { ...row, status: event.status }
                : row,
            )
          : current,
      );
    }).then((fn) => {
      unlisten = fn;
    });
    return () => {
      unlisten?.();
    };
  }, []);

  const subAgents = useMemo(
    () => (runs ?? []).filter((r) => r.parentRunId !== null),
    [runs],
  );

  return (
    <AgentsView
      runs={subAgents}
      loading={runs === null}
      error={error}
      busy={busy}
      onRefresh={() => void refresh()}
      onOpen={onOpen}
      onKill={(runId) => {
        void killRun(runId).catch(() => undefined);
      }}
    />
  );
}

export function AgentsView({
  runs,
  loading,
  error,
  busy,
  onRefresh,
  onOpen,
  onKill,
}: {
  runs: RunHeader[];
  loading: boolean;
  error: string | null;
  busy: boolean;
  onRefresh: () => void;
  onOpen?: (runId: string) => void;
  onKill?: (runId: string) => void;
}) {
  const active = runs.filter((r) => ACTIVE_STATUSES.includes(r.status));
  const terminal = runs
    .filter((r) => TERMINAL_STATUSES.includes(r.status))
    .sort(
      (a, b) =>
        (b.completedAtMs ?? b.startedAtMs) - (a.completedAtMs ?? a.startedAtMs),
    );

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex items-center justify-between gap-3 border-b border-border bg-surface px-4 py-2">
        <div className="flex items-center gap-2">
          <Compass aria-hidden className="size-4 text-muted-foreground" />
          <h2 className="text-sm font-medium">Agents</h2>
        </div>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          aria-label="Refresh agents"
          onClick={onRefresh}
          disabled={busy}
        >
          <RefreshCw
            aria-hidden
            className={cn("size-4", busy && "animate-spin")}
          />
        </Button>
      </header>

      <div className="flex-1 overflow-y-auto px-4 py-4">
        {error ? (
          <p
            role="alert"
            className="mb-4 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-foreground"
          >
            {error}
          </p>
        ) : null}

        {loading ? (
          <p className="text-sm text-muted-foreground">Loading agents…</p>
        ) : runs.length === 0 ? (
          <EmptyState />
        ) : (
          <div className="space-y-6">
            <Section
              title={`Active (${active.length})`}
              runs={active}
              onOpen={onOpen}
              onKill={onKill}
            />
            <Section
              title={`Recent (${terminal.length})`}
              runs={terminal.slice(0, 50)}
              onOpen={onOpen}
            />
          </div>
        )}
      </div>
    </div>
  );
}

function EmptyState() {
  return (
    <div className="mx-auto flex max-w-md flex-col items-center gap-3 rounded-md border border-dashed border-border px-6 py-12 text-center">
      <Bot aria-hidden className="size-8 text-muted-foreground" />
      <h3 className="text-sm font-medium">No sub-agents yet</h3>
      <p className="text-xs text-muted-foreground">
        Sub-agents appear here when the brain delegates work. Every
        delegated task gets its own status, plan, and action log.
      </p>
    </div>
  );
}

function Section({
  title,
  runs,
  onOpen,
  onKill,
}: {
  title: string;
  runs: RunHeader[];
  onOpen?: (runId: string) => void;
  onKill?: (runId: string) => void;
}) {
  if (runs.length === 0) {
    return (
      <section aria-label={title}>
        <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          {title}
        </h3>
        <p className="text-xs text-muted-foreground">No agents in this section.</p>
      </section>
    );
  }
  return (
    <section aria-label={title}>
      <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
        {title}
      </h3>
      <ul className="space-y-2">
        {runs.map((run) => (
          <li key={run.runId}>
            <AgentRow run={run} onOpen={onOpen} onKill={onKill} />
          </li>
        ))}
      </ul>
    </section>
  );
}

function AgentRow({
  run,
  onOpen,
  onKill,
}: {
  run: RunHeader;
  onOpen?: (runId: string) => void;
  onKill?: (runId: string) => void;
}) {
  const isTerminal = TERMINAL_STATUSES.includes(run.status);
  const startedAgo = formatRelative(run.startedAtMs);
  return (
    <div className="rounded-md border border-border bg-card p-3 transition-colors hover:border-muted-foreground">
      <div className="flex items-start gap-3">
        <button
          type="button"
          onClick={() => onOpen?.(run.runId)}
          className="flex flex-1 flex-col items-start gap-1 text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded-sm"
        >
          <div className="flex flex-wrap items-center gap-2">
            <Bot aria-hidden className="size-4 text-muted-foreground" />
            <span className="text-sm font-medium">{run.title || "Sub-agent"}</span>
            <Badge tone={STATUS_TONE[run.status]}>
              {STATUS_LABEL[run.status]}
            </Badge>
          </div>
          <p className="font-mono text-[11px] text-muted-foreground">
            {run.runId}
          </p>
          <p className="text-xs text-muted-foreground">
            Started {startedAgo}
            {run.completedAtMs
              ? ` · finished ${formatRelative(run.completedAtMs)}`
              : null}
          </p>
        </button>
        {!isTerminal && onKill ? (
          <Button
            type="button"
            size="sm"
            variant="ghost"
            onClick={() => onKill(run.runId)}
            aria-label={`Kill ${run.title || run.runId}`}
          >
            Kill
          </Button>
        ) : null}
      </div>
      {(run.budget && run.budgetConsumed) || run.driftScore > 0 ? (
        <div className="mt-3 border-t border-border/50 pt-3">
          <BudgetMeter
            budget={run.budget ?? null}
            consumed={run.budgetConsumed ?? null}
            driftScore={run.driftScore}
          />
        </div>
      ) : null}
    </div>
  );
}

function formatRelative(epochMs: number): string {
  const delta = Date.now() - epochMs;
  if (delta < 0) return "just now";
  const seconds = Math.floor(delta / 1000);
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}
