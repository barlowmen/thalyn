import { AlertTriangle, ShieldAlert, ShieldCheck } from "lucide-react";

import type { ConfidencePayload } from "@/components/chat/types";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

type Props = {
  confidence: ConfidencePayload;
  /**
   * Click handler the parent wires to the drill-into-source UX
   * (F1.10) — opens the relevant drawer at the audit's ``sourceRef``.
   * Optional so Storybook fixtures and tests can render the pill
   * without a router.
   */
  onDrill?: (audit: ConfidencePayload["audit"]) => void;
};

/**
 * Renders the lead-confidence indicator (F12.8) next to a delegated
 * reply. ``high`` confidence is intentionally invisible — the pill
 * fires only when there's something the user should notice. The
 * ``low`` variant doubles as the drill-into-source affordance for
 * the audit's underlying claim.
 */
export function ConfidencePill({ confidence, onDrill }: Props) {
  if (confidence.level === "high") return null;

  const isLow = confidence.level === "low";
  const Icon = isLow ? ShieldAlert : AlertTriangle;
  const tone = isLow ? "danger" : "warning";
  const labelText = isLow ? "Low confidence" : "Check this";

  const tooltip = [
    `${confidence.audit.summary}.`,
    `Audit: ${confidence.audit.mode}.`,
    `Drift ${(confidence.audit.driftScore * 100).toFixed(0)}%.`,
    onDrill ? "Click to view source." : null,
  ]
    .filter(Boolean)
    .join(" ");

  const content = (
    <>
      <Icon aria-hidden className="size-3" />
      <span>{labelText}</span>
    </>
  );

  if (onDrill) {
    return (
      <button
        type="button"
        className={cn(
          "inline-flex items-center gap-1.5",
          "rounded-md border px-2 py-0.5 text-xs font-medium",
          "transition-colors focus-visible:outline-none focus-visible:ring-2",
          "focus-visible:ring-ring focus-visible:ring-offset-1",
          isLow
            ? "border-destructive/40 bg-destructive/15 hover:bg-destructive/25"
            : "border-warning/40 bg-warning/15 hover:bg-warning/25",
        )}
        title={tooltip}
        aria-label={`${labelText}: ${confidence.audit.summary}. View source.`}
        onClick={() => onDrill(confidence.audit)}
      >
        {content}
      </button>
    );
  }

  return (
    <Badge
      tone={tone}
      title={tooltip}
      aria-label={`${labelText}: ${confidence.audit.summary}.`}
      className="inline-flex items-center gap-1.5"
    >
      <ShieldCheck aria-hidden className="hidden" />
      {content}
    </Badge>
  );
}
