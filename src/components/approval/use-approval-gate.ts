import { useEffect, useState } from "react";

import { type Plan, subscribeRunApprovalRequired } from "@/lib/runs";

export type ApprovalGate = {
  runId: string;
  plan: Plan;
};

/**
 * Subscribes to run:approval_required for the lifetime of the
 * component. The latest event becomes the active gate; the consumer
 * clears it once the user has resolved it.
 */
export function useApprovalGate(): {
  gate: ApprovalGate | null;
  clear: () => void;
} {
  const [gate, setGate] = useState<ApprovalGate | null>(null);

  useEffect(() => {
    let active = true;
    let unlisten: (() => void) | undefined;
    subscribeRunApprovalRequired((event) => {
      if (!active) return;
      setGate({ runId: event.runId, plan: event.plan });
    })
      .then((fn) => {
        if (!active) {
          fn();
          return;
        }
        unlisten = fn;
      })
      .catch(() => {
        // No Tauri bridge in storybook / playwright; nothing to do.
      });
    return () => {
      active = false;
      unlisten?.();
    };
  }, []);

  return {
    gate,
    clear: () => setGate(null),
  };
}
