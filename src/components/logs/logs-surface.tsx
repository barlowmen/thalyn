import { Filter, RefreshCw, ScrollText } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { SurfaceCloseButton } from "@/components/shell/surface-close";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  type RunHeader,
  type RunStatus,
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

const ALL_STATUSES: RunStatus[] = [
  "running",
  "planning",
  "awaiting_approval",
  "paused",
  "pending",
  "completed",
  "errored",
  "killed",
];

/**
 * Runs index — every run the brain has produced, newest first,
 * filterable by status. Click a row to drill into the same
 * detail surface sub-agents use, so the inspector tree, plan,
 * and action log all work for historical runs too.
 */
export function LogsSurface({
  onOpen,
  onClose,
}: {
  onOpen?: (runId: string) => void;
  onClose?: () => void;
}) {
  const [runs, setRuns] = useState<RunHeader[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [filter, setFilter] = useState<RunStatus[]>([]);

  const refresh = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const result = await listRuns({
        statuses: filter.length > 0 ? filter : undefined,
        limit: 200,
      });
      setRuns(result.runs);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setRuns([]);
    } finally {
      setBusy(false);
    }
  }, [filter]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

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

  const sorted = useMemo(
    () => [...(runs ?? [])].sort((a, b) => b.startedAtMs - a.startedAtMs),
    [runs],
  );

  return (
    <LogsView
      runs={sorted}
      loading={runs === null}
      error={error}
      busy={busy}
      filter={filter}
      onFilterChange={setFilter}
      onRefresh={() => void refresh()}
      onOpen={onOpen}
      onClose={onClose}
    />
  );
}

export function LogsView({
  runs,
  loading,
  error,
  busy,
  filter,
  onFilterChange,
  onRefresh,
  onOpen,
  onClose,
}: {
  runs: RunHeader[];
  loading: boolean;
  error: string | null;
  busy: boolean;
  filter: RunStatus[];
  onFilterChange: (next: RunStatus[]) => void;
  onRefresh: () => void;
  onOpen?: (runId: string) => void;
  onClose?: () => void;
}) {
  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex items-center justify-between gap-3 border-b border-border bg-surface px-4 py-2">
        <div className="flex items-center gap-2">
          <ScrollText aria-hidden className="size-4 text-muted-foreground" />
          <h2 className="text-sm font-medium">Logs</h2>
        </div>
        <div className="flex items-center gap-1">
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label="Refresh logs"
            onClick={onRefresh}
            disabled={busy}
          >
            <RefreshCw
              aria-hidden
              className={cn("size-4", busy && "animate-spin")}
            />
          </Button>
          <SurfaceCloseButton onClose={onClose} />
        </div>
      </header>

      <div className="border-b border-border bg-surface px-4 py-2">
        <FilterRow filter={filter} onChange={onFilterChange} />
      </div>

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
          <p className="text-sm text-muted-foreground">Loading runs…</p>
        ) : runs.length === 0 ? (
          <EmptyState filtered={filter.length > 0} />
        ) : (
          <ul className="space-y-2">
            {runs.map((run) => (
              <li key={run.runId}>
                <RunRow run={run} onOpen={onOpen} />
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function FilterRow({
  filter,
  onChange,
}: {
  filter: RunStatus[];
  onChange: (next: RunStatus[]) => void;
}) {
  const toggle = (status: RunStatus) => {
    if (filter.includes(status)) {
      onChange(filter.filter((s) => s !== status));
    } else {
      onChange([...filter, status]);
    }
  };
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Filter aria-hidden className="size-3.5 text-muted-foreground" />
      <span className="text-xs text-muted-foreground">Filter:</span>
      {ALL_STATUSES.map((status) => {
        const active = filter.includes(status);
        return (
          <button
            key={status}
            type="button"
            onClick={() => toggle(status)}
            aria-pressed={active}
            className={cn(
              "rounded-full border px-2 py-0.5 text-[11px] transition-colors",
              active
                ? "border-accent bg-accent text-accent-foreground"
                : "border-border bg-card text-muted-foreground hover:border-muted-foreground",
            )}
          >
            {STATUS_LABEL[status]}
          </button>
        );
      })}
      {filter.length > 0 ? (
        <button
          type="button"
          onClick={() => onChange([])}
          className="text-[11px] text-muted-foreground underline underline-offset-2"
        >
          Clear
        </button>
      ) : null}
    </div>
  );
}

function EmptyState({ filtered }: { filtered: boolean }) {
  return (
    <div className="mx-auto flex max-w-md flex-col items-center gap-3 rounded-md border border-dashed border-border px-6 py-12 text-center">
      <ScrollText aria-hidden className="size-8 text-muted-foreground" />
      <h3 className="text-sm font-medium">
        {filtered ? "No matching runs" : "No runs yet"}
      </h3>
      <p className="text-xs text-muted-foreground">
        {filtered
          ? "Try clearing the filter, or pick a different status."
          : "Every brain interaction creates a run. Start a chat and runs will appear here with their plan, status, and action log."}
      </p>
    </div>
  );
}

function RunRow({
  run,
  onOpen,
}: {
  run: RunHeader;
  onOpen?: (runId: string) => void;
}) {
  return (
    <button
      type="button"
      onClick={() => onOpen?.(run.runId)}
      className="block w-full rounded-md border border-border bg-card p-3 text-left transition-colors hover:border-muted-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium">
          {run.title || "Untitled run"}
        </span>
        <Badge tone={STATUS_TONE[run.status]}>{STATUS_LABEL[run.status]}</Badge>
        {run.parentRunId ? (
          <Badge tone="muted">sub-agent</Badge>
        ) : (
          <Badge tone="muted">top-level</Badge>
        )}
        {run.driftScore > 0 ? (
          <Badge tone={run.driftScore > 0.5 ? "danger" : "warning"}>
            drift {(run.driftScore * 100).toFixed(0)}%
          </Badge>
        ) : null}
      </div>
      <div className="mt-1 flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
        <span className="font-mono text-[11px]">{run.runId}</span>
        <span>·</span>
        <span>{run.providerId}</span>
        <span>·</span>
        <span>started {formatTimestamp(run.startedAtMs)}</span>
        {run.completedAtMs ? (
          <>
            <span>·</span>
            <span>finished {formatTimestamp(run.completedAtMs)}</span>
          </>
        ) : null}
      </div>
    </button>
  );
}

function formatTimestamp(epochMs: number): string {
  const date = new Date(epochMs);
  const sameDay = new Date().toDateString() === date.toDateString();
  if (sameDay) {
    return date.toLocaleTimeString(undefined, {
      hour: "2-digit",
      minute: "2-digit",
    });
  }
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}
