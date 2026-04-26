import { Activity } from "lucide-react";

import { ActionLog } from "@/components/inspector/action-log";
import { PlanTree } from "@/components/inspector/plan-tree";
import { useActiveRun } from "@/components/inspector/use-active-run";
import { Badge } from "@/components/ui/badge";
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

/**
 * Inspector panel — live view of the active run's plan, status, and
 * action log. Subscribes to the run.* Tauri events; resets on a new
 * run id. Empty state stays in place when no run has started yet.
 */
export function InspectorPanel() {
  const run = useActiveRun();

  return (
    <aside
      aria-label="Inspector"
      className="flex h-full flex-col gap-4 overflow-y-auto bg-surface px-4 py-4"
    >
      <header className="flex items-center justify-between gap-2">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          Inspector
        </h2>
        {run && (
          <Badge tone={STATUS_TONE[run.status]}>
            {STATUS_LABEL[run.status]}
          </Badge>
        )}
      </header>

      {!run ? (
        <Empty />
      ) : (
        <div className="space-y-5">
          <RunHeader runId={run.runId} />

          <Section title="Plan">
            {run.plan ? (
              <>
                {run.plan.goal && (
                  <p className="mb-2 text-xs text-muted-foreground">
                    {run.plan.goal}
                  </p>
                )}
                <PlanTree plan={run.plan} />
              </>
            ) : (
              <p className="text-xs text-muted-foreground">
                Generating plan…
              </p>
            )}
          </Section>

          <Section title="Action log">
            <ActionLog entries={run.actionLog} />
          </Section>
        </div>
      )}
    </aside>
  );
}

function Empty() {
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-3 rounded-md border border-dashed border-border px-4 py-8 text-center">
      <Activity className="h-8 w-8 text-muted-foreground" aria-hidden />
      <div>
        <p className="text-sm">No agents running.</p>
        <p className="text-xs text-muted-foreground">
          Send a message; the plan, status, and action log appear here as
          the run unfolds.
        </p>
      </div>
    </div>
  );
}

function RunHeader({ runId }: { runId: string }) {
  return (
    <p className="font-mono text-[11px] text-muted-foreground">
      run · {runId}
    </p>
  );
}

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section>
      <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
        {title}
      </h3>
      {children}
    </section>
  );
}
