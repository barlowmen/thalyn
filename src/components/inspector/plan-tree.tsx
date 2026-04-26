import { Check, CircleDashed, Loader2, X } from "lucide-react";

import type { Plan, PlanNode, PlanNodeStatus } from "@/lib/runs";
import { cn } from "@/lib/utils";

export function PlanTree({ plan }: { plan: Plan }) {
  if (plan.nodes.length === 0) {
    return (
      <p className="text-xs text-muted-foreground">
        Plan has no steps yet.
      </p>
    );
  }
  return (
    <ol className="space-y-2" aria-label="Plan steps">
      {plan.nodes.map((node) => (
        <PlanNodeRow key={node.id} node={node} />
      ))}
    </ol>
  );
}

function PlanNodeRow({ node }: { node: PlanNode }) {
  return (
    <li
      className={cn(
        "rounded-md border border-border bg-card px-3 py-2",
        node.status === "errored" && "border-destructive/40",
      )}
    >
      <div className="flex items-start gap-2">
        <PlanStatusIcon status={node.status} />
        <div className="flex-1 space-y-1">
          <p className="text-sm leading-tight">{node.description}</p>
          {node.rationale && (
            <p className="text-xs text-muted-foreground">{node.rationale}</p>
          )}
        </div>
      </div>
    </li>
  );
}

function PlanStatusIcon({ status }: { status: PlanNodeStatus }) {
  const className = "h-4 w-4 shrink-0 mt-0.5";
  switch (status) {
    case "done":
      return <Check className={cn(className, "text-success")} aria-label="Done" />;
    case "in_progress":
      return (
        <Loader2
          className={cn(className, "text-primary animate-spin")}
          aria-label="In progress"
        />
      );
    case "errored":
      return (
        <X className={cn(className, "text-destructive")} aria-label="Errored" />
      );
    case "skipped":
      return (
        <CircleDashed
          className={cn(className, "text-muted-foreground")}
          aria-label="Skipped"
        />
      );
    case "pending":
    default:
      return (
        <CircleDashed
          className={cn(className, "text-muted-foreground")}
          aria-label="Pending"
        />
      );
  }
}
