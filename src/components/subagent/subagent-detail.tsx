import { ArrowLeft, Bot } from "lucide-react";

import { ActionLog } from "@/components/inspector/action-log";
import { BudgetMeter } from "@/components/inspector/budget-meter";
import { PlanTree } from "@/components/inspector/plan-tree";
import { useRunDetail } from "@/components/inspector/use-run-detail";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { killRun, type RunStatus, type SandboxTier } from "@/lib/runs";

const TIER_LABEL: Record<SandboxTier, string> = {
  tier_0: "Tier 0 — bare process",
  tier_1: "Tier 1 — devcontainer + worktree",
  tier_2: "Tier 2 — microVM",
  tier_3: "Tier 3 — cloud sandbox",
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

const TERMINAL_STATUSES: RunStatus[] = ["completed", "errored", "killed"];

type Props = {
  runId: string;
  onClose: () => void;
  onTakeOver?: (runId: string) => void;
};

/**
 * Main-panel detail view for a single sub-agent. Shows the run's
 * plan, status, and action log; offers kill + take-over controls
 * while the run is in flight. ``onClose`` returns the user to the
 * parent chat surface.
 */
export function SubAgentDetail({ runId, onClose, onTakeOver }: Props) {
  const detail = useRunDetail(runId);
  const status = detail?.status ?? "pending";
  const isTerminal = TERMINAL_STATUSES.includes(status);

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center justify-between border-b border-border bg-background px-6 py-3">
        <div className="flex items-center gap-3 overflow-hidden">
          <Button
            size="sm"
            variant="ghost"
            onClick={onClose}
            aria-label="Close sub-agent"
          >
            <ArrowLeft className="h-4 w-4" aria-hidden />
          </Button>
          <Bot className="h-4 w-4 text-muted-foreground" aria-hidden />
          <div className="overflow-hidden">
            <h2 className="truncate text-sm font-semibold">
              {detail?.title ?? "Sub-agent"}
            </h2>
            <p className="truncate font-mono text-[11px] text-muted-foreground">
              {runId}
            </p>
          </div>
          <Badge tone={STATUS_TONE[status]}>{STATUS_LABEL[status]}</Badge>
          {detail?.sandboxTier && (
            <Badge tone="muted">{TIER_LABEL[detail.sandboxTier]}</Badge>
          )}
        </div>
        <div className="flex items-center gap-2">
          {!isTerminal && (
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                void killRun(runId).catch(() => undefined);
              }}
            >
              Kill
            </Button>
          )}
          {onTakeOver && (
            <Button size="sm" variant="default" onClick={() => onTakeOver(runId)}>
              Take over
            </Button>
          )}
        </div>
      </header>

      <div className="flex-1 overflow-y-auto px-6 py-4">
        {detail && (detail.budget || detail.driftScore > 0) && (
          <Section title="Budget &amp; drift">
            <BudgetMeter
              budget={detail.budget}
              consumed={detail.budgetConsumed}
              driftScore={detail.driftScore}
              variant="full"
            />
          </Section>
        )}

        <Section title="Plan">
          {detail?.plan ? (
            <>
              {detail.plan.goal && (
                <p className="mb-2 text-xs text-muted-foreground">
                  {detail.plan.goal}
                </p>
              )}
              <PlanTree plan={detail.plan} />
            </>
          ) : (
            <p className="text-xs text-muted-foreground">No plan recorded.</p>
          )}
        </Section>

        <Section title="Action log">
          <ActionLog entries={detail?.actionLog ?? []} />
        </Section>

        {detail?.finalResponse && (
          <Section title="Final response">
            <p className="whitespace-pre-wrap text-sm">{detail.finalResponse}</p>
          </Section>
        )}
      </div>
    </div>
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
    <section className="mb-5">
      <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
        {title}
      </h3>
      {children}
    </section>
  );
}
