import { useEffect, useState } from "react";

import {
  type ActionLogEntry,
  type Budget,
  type BudgetConsumed,
  type Plan,
  type RunHeader,
  type RunStatus,
  type SandboxTier,
  getRun,
  subscribeRunActionLog,
  subscribeRunPlanUpdate,
  subscribeRunStatus,
} from "@/lib/runs";

const ACTION_LOG_CAP = 200;

export type RunDetail = {
  runId: string;
  status: RunStatus;
  title: string;
  parentRunId: string | null;
  plan: Plan | null;
  actionLog: ActionLogEntry[];
  finalResponse: string;
  sandboxTier: SandboxTier | null;
  driftScore: number;
  budget: Budget | null;
  budgetConsumed: BudgetConsumed | null;
};

/**
 * Track a single run's live state by ``runId``. Seeded from
 * ``runs.get`` and refreshed in place by the run.* notifications
 * filtered to that id. Returns ``null`` until the initial fetch
 * resolves so the renderer can show a loading state.
 */
export function useRunDetail(runId: string | null): RunDetail | null {
  const [detail, setDetail] = useState<RunDetail | null>(null);

  useEffect(() => {
    if (!runId) {
      setDetail(null);
      return;
    }
    let active = true;
    setDetail(null);

    void getRun(runId).then((header) => {
      if (!active || !header) return;
      setDetail(detailFromHeader(header));
    });

    const subStatus = subscribeRunStatus((event) => {
      if (!active || event.runId !== runId) return;
      setDetail((current) =>
        current ? { ...current, status: event.status } : current,
      );
    });

    const subPlan = subscribeRunPlanUpdate((event) => {
      if (!active || event.runId !== runId) return;
      setDetail((current) =>
        current ? { ...current, plan: event.plan } : current,
      );
    });

    const subLog = subscribeRunActionLog((event) => {
      if (!active || event.runId !== runId) return;
      setDetail((current) => {
        if (!current) return current;
        const next = [...current.actionLog, event.entry];
        if (next.length > ACTION_LOG_CAP) {
          next.splice(0, next.length - ACTION_LOG_CAP);
        }
        return { ...current, actionLog: next };
      });
    });

    return () => {
      active = false;
      void subStatus.then((fn) => fn());
      void subPlan.then((fn) => fn());
      void subLog.then((fn) => fn());
    };
  }, [runId]);

  return detail;
}

function detailFromHeader(header: RunHeader): RunDetail {
  return {
    runId: header.runId,
    status: header.status,
    title: header.title,
    parentRunId: header.parentRunId,
    plan: header.plan,
    actionLog: [],
    finalResponse: header.finalResponse,
    sandboxTier: header.sandboxTier ?? null,
    driftScore: header.driftScore ?? 0,
    budget: header.budget ?? null,
    budgetConsumed: header.budgetConsumed ?? null,
  };
}
