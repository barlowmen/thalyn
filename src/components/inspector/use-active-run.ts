import { useEffect, useState } from "react";

import {
  type ActionLogEntry,
  type Plan,
  type RunStatus,
  subscribeRunActionLog,
  subscribeRunPlanUpdate,
  subscribeRunStatus,
} from "@/lib/runs";

const ACTION_LOG_CAP = 200;

export type ActiveRun = {
  runId: string;
  status: RunStatus;
  plan: Plan | null;
  actionLog: ActionLogEntry[];
};

/**
 * Tracks the currently-active run by subscribing to the three
 * run.* Tauri events. The first run.status event for a new runId
 * resets the in-memory plan + action log; subsequent events for the
 * same id append. The action log is capped at ACTION_LOG_CAP entries
 * to keep the inspector responsive on long runs.
 */
export function useActiveRun(): ActiveRun | null {
  const [run, setRun] = useState<ActiveRun | null>(null);

  useEffect(() => {
    let unstatus: (() => void) | undefined;
    let unplan: (() => void) | undefined;
    let unlog: (() => void) | undefined;
    let active = true;

    const setSafely = <T,>(fn: () => Promise<T>) =>
      fn().then((res) => {
        if (!active) {
          (res as unknown as () => void)?.();
          return undefined;
        }
        return res;
      });

    setSafely(() =>
      subscribeRunStatus((event) => {
        setRun((current) => {
          if (current?.runId !== event.runId) {
            // New run takes over the inspector.
            return {
              runId: event.runId,
              status: event.status,
              plan: null,
              actionLog: [],
            };
          }
          return { ...current, status: event.status };
        });
      }),
    ).then((fn) => {
      unstatus = fn as (() => void) | undefined;
    });

    setSafely(() =>
      subscribeRunPlanUpdate((event) => {
        setRun((current) => {
          const base = current ?? {
            runId: event.runId,
            status: "planning" as RunStatus,
            plan: null,
            actionLog: [],
          };
          if (base.runId !== event.runId) return base;
          return { ...base, plan: event.plan };
        });
      }),
    ).then((fn) => {
      unplan = fn as (() => void) | undefined;
    });

    setSafely(() =>
      subscribeRunActionLog((event) => {
        setRun((current) => {
          const base = current ?? {
            runId: event.runId,
            status: "running" as RunStatus,
            plan: null,
            actionLog: [],
          };
          if (base.runId !== event.runId) return base;
          const next = [...base.actionLog, event.entry];
          // Keep most recent ACTION_LOG_CAP entries.
          if (next.length > ACTION_LOG_CAP) {
            next.splice(0, next.length - ACTION_LOG_CAP);
          }
          return { ...base, actionLog: next };
        });
      }),
    ).then((fn) => {
      unlog = fn as (() => void) | undefined;
    });

    return () => {
      active = false;
      unstatus?.();
      unplan?.();
      unlog?.();
    };
  }, []);

  return run;
}
