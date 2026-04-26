import { AlertTriangle, X } from "lucide-react";

import type { DriftGate } from "@/components/inspector/use-drift-gate";
import { Button } from "@/components/ui/button";

type Props = {
  gate: DriftGate | null;
  onReview?: (runId: string) => void;
  onDismiss: () => void;
};

/**
 * Banner shown when the critic-driven drift gate fires. The run
 * itself has already halted in the brain; this surfaces the verdict
 * and lets the user open the run for review or dismiss the banner.
 */
export function DriftGateBanner({ gate, onReview, onDismiss }: Props) {
  if (!gate) return null;
  const percent = Math.round(gate.driftScore * 100);
  return (
    <div
      role="alert"
      className="flex items-start gap-3 border-b border-warning/30 bg-warning/10 px-6 py-3 text-sm"
    >
      <AlertTriangle className="mt-0.5 h-4 w-4 text-warning" aria-hidden />
      <div className="flex-1 space-y-1">
        <p className="font-medium">
          Drift detected at {gate.threshold} budget — review run
        </p>
        <p className="text-xs text-muted-foreground">
          Combined drift score {percent}%. {gate.reason}
        </p>
        <div className="mt-2 flex items-center gap-2">
          {onReview && (
            <Button
              size="sm"
              variant="default"
              onClick={() => {
                onReview(gate.runId);
                onDismiss();
              }}
            >
              Review run
            </Button>
          )}
          <Button size="sm" variant="ghost" onClick={onDismiss}>
            Dismiss
          </Button>
        </div>
      </div>
      <Button
        size="sm"
        variant="ghost"
        aria-label="Close"
        onClick={onDismiss}
      >
        <X className="h-3.5 w-3.5" aria-hidden />
      </Button>
    </div>
  );
}
