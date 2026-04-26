import { useEffect, useState } from "react";

import { listen, type UnlistenFn } from "@tauri-apps/api/event";

export type DriftGate = {
  runId: string;
  threshold: string;
  driftScore: number;
  reason: string;
};

type RawApprovalEvent = {
  runId: string;
  gateKind: string;
  threshold?: string;
  driftScore?: number;
  reason?: string;
};

/**
 * Track the most recent drift-gate notification. The brain emits
 * ``run.approval_required`` with ``gateKind: "drift"`` when the
 * critic's combined drift score crosses the pause threshold; the
 * renderer shows a CTA so the user can review what triggered it.
 *
 * Returns ``{gate, dismiss}`` — call ``dismiss`` to clear the banner
 * once the user has acted on it.
 */
export function useDriftGate(): {
  gate: DriftGate | null;
  dismiss: () => void;
} {
  const [gate, setGate] = useState<DriftGate | null>(null);

  useEffect(() => {
    let active = true;
    let unlisten: UnlistenFn | undefined;

    listen<RawApprovalEvent>("run:approval_required", (event) => {
      const payload = event.payload;
      if (!active) return;
      if (payload.gateKind !== "drift") return;
      setGate({
        runId: payload.runId,
        threshold: payload.threshold ?? "",
        driftScore: payload.driftScore ?? 0,
        reason: payload.reason ?? "",
      });
    })
      .then((fn) => {
        if (!active) {
          fn();
          return;
        }
        unlisten = fn;
      })
      .catch(() => {
        // No-op outside Tauri (storybook / playwright).
      });

    return () => {
      active = false;
      unlisten?.();
    };
  }, []);

  return { gate, dismiss: () => setGate(null) };
}
