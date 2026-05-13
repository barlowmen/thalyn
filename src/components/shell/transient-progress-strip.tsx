import { AlertTriangle, GaugeCircle, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export type TransientActivityKind =
  /** A user turn is being routed through the brain — pre-run latency. */
  | "sending"
  /** A run (lead or worker) is in flight. */
  | "running"
  /** A run is waiting for the user to approve a plan. */
  | "awaiting_approval"
  /** The drift monitor flagged a run that needs human review. */
  | "drift";

export type TransientActivity = {
  kind: TransientActivityKind;
  /** Short, action-oriented label. F8.3 calls for a single visible
   *  signal that's "unobtrusive when absent and unmissable when
   *  present"; the label IS the signal, so write it short and direct. */
  label: string;
  /** Optional click handler. When present the row becomes a button
   *  and clicking it should open the relevant detail drawer (the
   *  drawer host lands later; pre-drawer the handler can also no-op
   *  or surface a toast). */
  onClick?: () => void;
};

/**
 * The transient progress strip (F8.3). Lives between the eternal
 * chat region and the composer, and is **only** rendered when an
 * activity is present — the parent passes ``null`` to keep the
 * region collapsed.
 *
 * Visually a single line, ~36 px tall, with a small leading
 * indicator and the activity label. When ``onClick`` is wired the
 * row becomes a button so screen-reader and keyboard users can drive
 * the click → drawer flow once drawers exist.
 *
 * The component never animates in/out — entry/exit are purely
 * structural so reduced-motion is honoured by construction. The
 * spinning indicator on the ``sending`` and ``running`` kinds uses
 * the global ``animate-spin`` utility, which the
 * ``prefers-reduced-motion`` rule in ``globals.css`` neutralises.
 */
export function TransientProgressStrip({
  activity,
}: {
  activity: TransientActivity | null;
}) {
  if (!activity) return null;
  const { Icon, tone } = visualForKind(activity.kind);

  const content = (
    <div className="flex items-center gap-2 truncate">
      <Icon
        aria-hidden
        className={cn(
          "h-3.5 w-3.5 shrink-0",
          tone,
          (activity.kind === "sending" || activity.kind === "running") &&
            "animate-spin",
        )}
      />
      <span className="truncate text-xs">{activity.label}</span>
    </div>
  );

  if (activity.onClick) {
    return (
      <div
        role="status"
        aria-live="polite"
        className="flex h-9 shrink-0 items-center gap-2 border-t border-border bg-surface px-4"
      >
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={activity.onClick}
          aria-label={`${activity.label}. Open detail.`}
          className="h-7 w-full justify-start truncate px-2 text-xs"
        >
          {content}
        </Button>
      </div>
    );
  }

  return (
    <div
      role="status"
      aria-live="polite"
      className="flex h-9 shrink-0 items-center border-t border-border bg-surface px-4"
    >
      {content}
    </div>
  );
}

function visualForKind(kind: TransientActivityKind): {
  Icon: typeof Loader2;
  tone: string;
} {
  switch (kind) {
    case "sending":
    case "running":
      return { Icon: Loader2, tone: "text-muted-foreground" };
    case "awaiting_approval":
      return { Icon: GaugeCircle, tone: "text-warning" };
    case "drift":
      return { Icon: AlertTriangle, tone: "text-danger" };
  }
}
