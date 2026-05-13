import { Bot, Brain, MessageSquare, MessageSquareDashed, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  type LeadAgent,
  type LeadStatus,
  listLeads,
} from "@/lib/leads";
import {
  listMemory,
  type MemoryEntry,
} from "@/lib/memory";
import {
  type RunHeader,
  type RunStatus,
  listRuns,
  subscribeRunStatus,
} from "@/lib/runs";
import { dispatchToolsOpen } from "@/components/shell/drawer-host";

const LEAD_STATUS_TONE: Record<
  LeadStatus,
  "default" | "success" | "warning" | "danger" | "muted"
> = {
  active: "success",
  paused: "warning",
  archived: "muted",
};

const LEAD_STATUS_LABEL: Record<LeadStatus, string> = {
  active: "Active",
  paused: "Paused",
  archived: "Archived",
};

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

const ACTIVE_STATUSES: ReadonlySet<RunStatus> = new Set([
  "pending",
  "planning",
  "awaiting_approval",
  "running",
  "paused",
]);

type LeadFixture = {
  agent: LeadAgent;
  runs: RunHeader[];
  memory: MemoryEntry[];
};

/**
 * Drawer surface for a project lead. Composes a header (display name
 * + status badges), a worker-tile list rendered from the lead's
 * sub-runs, and a read-only memory inspector scoped to the lead's
 * project + agent namespace. Clicking a worker tile opens the worker
 * drawer pinned to that runId so the user can drill from lead → run
 * detail without leaving the chat.
 */
export function LeadSurface({
  agentId,
  fixture,
}: {
  agentId: string;
  /** Storybook / playwright fallback when the Tauri commands are
   *  unavailable — seeds the surface with a static fixture so the
   *  layout renders deterministically. */
  fixture?: LeadFixture;
}) {
  const [agent, setAgent] = useState<LeadAgent | null>(
    fixture?.agent ?? null,
  );
  const [runs, setRuns] = useState<RunHeader[]>(fixture?.runs ?? []);
  const [memory, setMemory] = useState<MemoryEntry[]>(fixture?.memory ?? []);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    if (fixture) return;
    setBusy(true);
    setError(null);
    try {
      const [leadResult, runsResult] = await Promise.all([
        listLeads(),
        listRuns({ parentLeadId: agentId, limit: 40 }),
      ]);
      const match =
        leadResult.agents.find((a) => a.agentId === agentId) ?? null;
      setAgent(match);
      setRuns(runsResult.runs);
      const projectId = match?.projectId ?? undefined;
      const memResult = await listMemory({
        projectId,
        scopes: ["project", "agent"],
        limit: 50,
      });
      setMemory(memResult.entries);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }, [agentId, fixture]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Live run-status updates — flip tile badges in place when the
  // brain reports a transition without forcing a full refresh.
  useEffect(() => {
    if (fixture) return;
    let unlisten: (() => void) | null = null;
    void subscribeRunStatus((event) => {
      setRuns((current) =>
        current.map((row) =>
          row.runId === event.runId ? { ...row, status: event.status } : row,
        ),
      );
    })
      .then((fn) => {
        unlisten = fn;
      })
      .catch(() => undefined);
    return () => {
      unlisten?.();
    };
  }, [fixture]);

  const active = useMemo(
    () => runs.filter((r) => ACTIVE_STATUSES.has(r.status)),
    [runs],
  );
  const recent = useMemo(
    () =>
      runs
        .filter((r) => !ACTIVE_STATUSES.has(r.status))
        .sort(
          (a, b) =>
            (b.completedAtMs ?? b.startedAtMs) -
            (a.completedAtMs ?? a.startedAtMs),
        )
        .slice(0, 8),
    [runs],
  );

  const aggregateDrift = useMemo(() => {
    if (active.length === 0) return 0;
    return Math.max(...active.map((r) => r.driftScore ?? 0));
  }, [active]);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex items-start gap-2 border-b border-border bg-surface px-4 py-2 pr-24">
        <Brain
          aria-hidden
          className="mt-0.5 size-4 shrink-0 text-muted-foreground"
        />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="truncate text-sm font-semibold">
              {agent?.displayName ?? "Lead"}
            </h2>
            {agent && (
              <Badge tone={LEAD_STATUS_TONE[agent.status]}>
                {LEAD_STATUS_LABEL[agent.status]}
              </Badge>
            )}
            {aggregateDrift >= 0.7 && (
              <Badge tone="warning">drift {Math.round(aggregateDrift * 100)}%</Badge>
            )}
          </div>
          <p className="mt-0.5 truncate font-mono text-[11px] text-muted-foreground">
            {agentId}
          </p>
        </div>
        <div className="flex items-center gap-1">
          {agent && (
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="h-7 gap-1 px-2 text-[11px]"
              onClick={() =>
                dispatchToolsOpen({
                  kind: "lead-chat",
                  params: {
                    agentId: agent.agentId,
                    displayName: agent.displayName,
                  },
                })
              }
            >
              <MessageSquare aria-hidden className="size-3" /> Direct chat
            </Button>
          )}
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="h-7 w-7"
            onClick={() => void refresh()}
            disabled={busy}
            aria-label="Refresh lead detail"
          >
            <RefreshCw aria-hidden className="size-3.5" />
          </Button>
        </div>
      </header>

      <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-4 py-3">
        {error && (
          <p role="alert" className="text-xs text-danger">
            {error}
          </p>
        )}

        <section className="space-y-1.5">
          <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            In flight ({active.length})
          </h3>
          {active.length === 0 ? (
            <p className="text-xs text-muted-foreground">
              No workers running right now.
            </p>
          ) : (
            <ul className="space-y-1.5">
              {active.map((run) => (
                <WorkerTile key={run.runId} run={run} />
              ))}
            </ul>
          )}
        </section>

        <section className="space-y-1.5">
          <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            Recent activity
          </h3>
          {recent.length === 0 ? (
            <p className="text-xs text-muted-foreground">
              No recent runs for this lead.
            </p>
          ) : (
            <ul className="space-y-1.5">
              {recent.map((run) => (
                <WorkerTile key={run.runId} run={run} />
              ))}
            </ul>
          )}
        </section>

        <section className="space-y-1.5">
          <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            Memory ({memory.length})
          </h3>
          {memory.length === 0 ? (
            <p className="text-xs text-muted-foreground">
              No project or agent-scoped memory entries yet.
            </p>
          ) : (
            <ul className="space-y-1.5">
              {memory.map((entry) => (
                <li
                  key={entry.memoryId}
                  className="rounded-md border border-border bg-card px-3 py-2"
                >
                  <div className="flex items-center gap-2 text-[11px] uppercase tracking-wider text-muted-foreground">
                    <Badge tone="muted">{entry.scope}</Badge>
                    <span className="font-mono">{entry.kind}</span>
                  </div>
                  <p className="mt-1 whitespace-pre-wrap text-xs">
                    {entry.body}
                  </p>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </div>
  );
}

function WorkerTile({ run }: { run: RunHeader }) {
  return (
    <li>
      <button
        type="button"
        className="flex w-full items-start gap-2 rounded-md border border-border bg-card px-3 py-2 text-left hover:border-primary/40"
        onClick={() =>
          dispatchToolsOpen({
            kind: "worker",
            params: { runId: run.runId },
          })
        }
      >
        <Bot aria-hidden className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="truncate text-xs font-medium">
              {run.title || "Untitled run"}
            </span>
            <Badge tone={STATUS_TONE[run.status]}>
              {STATUS_LABEL[run.status]}
            </Badge>
          </div>
          <p className="mt-0.5 truncate font-mono text-[10px] text-muted-foreground">
            {run.runId}
          </p>
        </div>
        <MessageSquareDashed
          aria-hidden
          className="mt-0.5 size-3 shrink-0 text-muted-foreground"
        />
      </button>
    </li>
  );
}
