import { Bot, Hammer, ThumbsUp, X } from "lucide-react";
import { useEffect, useState } from "react";

import { ActionLog } from "@/components/worker/action-log";
import { BudgetMeter } from "@/components/worker/budget-meter";
import { PlanTree } from "@/components/worker/plan-tree";
import {
  detailFromHeader,
  type RunDetail,
  useRunDetail,
} from "@/components/worker/use-run-detail";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  readActiveProvider,
  subscribeActiveProvider,
} from "@/lib/active-provider";
import {
  approvePlan,
  killRun,
  type RunHeader,
  type RunStatus,
  type SandboxTier,
} from "@/lib/runs";

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

const TIER_LABEL: Record<SandboxTier, string> = {
  tier_0: "Tier 0 — bare process",
  tier_1: "Tier 1 — devcontainer + worktree",
  tier_2: "Tier 2 — microVM",
  tier_3: "Tier 3 — cloud sandbox",
};

const TERMINAL_STATUSES: RunStatus[] = ["completed", "errored", "killed"];

/**
 * Drawer surface for an in-flight or recent worker run. Streams the
 * plan tree, action log, budget meter, and drift indicator off the
 * brain's run.* notifications scoped to ``runId``. The inline
 * approval button resolves the awaiting-approval gate without the
 * user having to chase the modal — the brain's resolve is
 * idempotent so a duplicate click against an already-resolved gate
 * is harmless.
 */
export function WorkerSurface({
  runId,
  providerId: providerIdProp,
  staticDetail,
}: {
  runId: string;
  /** Provider id used when the user resolves an inline approval gate
   *  from inside the drawer. Defaults to the live ``active-provider``
   *  selection, with the prop reserved for storybook stories that
   *  need a fixed value. */
  providerId?: string;
  /** Storybook / playwright fallback when the Tauri ``runs.get`` path
   *  is unavailable — seeds the surface synchronously with a fixture
   *  so the layout renders. */
  staticDetail?: RunDetail;
}) {
  const live = useRunDetail(runId);
  const detail = live ?? staticDetail ?? null;
  const [activeProvider, setActiveProvider] = useState<string>(() =>
    readActiveProvider(),
  );
  useEffect(() => subscribeActiveProvider(setActiveProvider), []);
  const providerId = providerIdProp ?? activeProvider;
  const isTerminal = detail
    ? TERMINAL_STATUSES.includes(detail.status)
    : false;
  const isAwaitingApproval = detail?.status === "awaiting_approval";

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex items-start gap-2 border-b border-border bg-surface px-4 py-2 pr-24">
        <Hammer aria-hidden className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="truncate text-sm font-semibold">
              {detail?.title ?? "Worker"}
            </h2>
            {detail && (
              <Badge tone={STATUS_TONE[detail.status]}>
                {STATUS_LABEL[detail.status]}
              </Badge>
            )}
            {detail?.sandboxTier && (
              <Badge tone="muted">{TIER_LABEL[detail.sandboxTier]}</Badge>
            )}
          </div>
          <p className="mt-0.5 truncate font-mono text-[11px] text-muted-foreground">
            {runId}
          </p>
        </div>
      </header>

      <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-4 py-3">
        {!detail && (
          <p
            role="status"
            aria-live="polite"
            className="text-xs text-muted-foreground"
          >
            Loading run state…
          </p>
        )}

        {detail && (
          <>
            <BudgetMeter
              budget={detail.budget}
              consumed={detail.budgetConsumed}
              driftScore={detail.driftScore}
              variant="full"
            />

            {isAwaitingApproval && detail.plan && (
              <div className="rounded-md border border-warning/50 bg-warning/10 px-3 py-2">
                <div className="flex items-start gap-2">
                  <Bot
                    aria-hidden
                    className="mt-0.5 size-4 shrink-0 text-warning"
                  />
                  <div className="flex-1 space-y-1.5">
                    <p className="text-xs font-medium">
                      Plan ready for review.
                    </p>
                    <p className="text-[11px] text-muted-foreground">
                      Approve to run it as-is, or open the plan-review
                      modal from the chat to edit steps before approval.
                    </p>
                    <div className="flex flex-wrap gap-2">
                      <Button
                        type="button"
                        size="sm"
                        onClick={() => {
                          if (!providerId) return;
                          void approvePlan({
                            runId,
                            providerId,
                            decision: "approve",
                          }).catch(() => undefined);
                        }}
                        disabled={!providerId}
                      >
                        <ThumbsUp aria-hidden /> Approve
                      </Button>
                      <Button
                        type="button"
                        size="sm"
                        variant="ghost"
                        onClick={() => {
                          if (!providerId) return;
                          void approvePlan({
                            runId,
                            providerId,
                            decision: "reject",
                          }).catch(() => undefined);
                        }}
                        disabled={!providerId}
                      >
                        <X aria-hidden /> Reject
                      </Button>
                    </div>
                  </div>
                </div>
              </div>
            )}

            <section className="space-y-2">
              <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                Plan
              </h3>
              {detail.plan ? (
                <PlanTree plan={detail.plan} />
              ) : (
                <p className="text-xs text-muted-foreground">
                  Plan has not arrived yet.
                </p>
              )}
            </section>

            <section className="space-y-2">
              <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                Action log
              </h3>
              <ActionLog entries={detail.actionLog} />
            </section>

            {detail.finalResponse && (
              <section className="space-y-1">
                <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                  Result
                </h3>
                <p className="whitespace-pre-wrap rounded-md border border-border bg-card px-3 py-2 text-xs">
                  {detail.finalResponse}
                </p>
              </section>
            )}

            {!isTerminal && (
              <div className="flex items-center justify-end">
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  onClick={() => {
                    void killRun(runId).catch(() => undefined);
                  }}
                >
                  Kill run
                </Button>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

export function detailFromFixture(header: RunHeader): RunDetail {
  return detailFromHeader(header);
}
