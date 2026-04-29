import { Check, ChevronDown, ChevronRight, CircleDashed, Loader2, X } from "lucide-react";
import { useMemo, useState } from "react";

import type { Plan, PlanNode, PlanNodeStatus } from "@/lib/runs";
import { cn } from "@/lib/utils";

/**
 * Plan-tree renderer. F2.7 / F8.8 call for a collapsible tree that
 * surfaces per-step rationale and estimated cost. The drawer host
 * keeps the surface mounted across dismiss/re-open, so collapse
 * state survives a round-trip.
 */
export function PlanTree({ plan }: { plan: Plan }) {
  const tree = useMemo(() => buildTree(plan.nodes), [plan.nodes]);
  if (plan.nodes.length === 0) {
    return (
      <p className="text-xs text-muted-foreground">
        Plan has no steps yet.
      </p>
    );
  }
  return (
    <ol className="space-y-1.5" aria-label="Plan steps">
      {tree.map((root) => (
        <PlanNodeRow key={root.node.id} entry={root} depth={0} />
      ))}
    </ol>
  );
}

type TreeEntry = { node: PlanNode; children: TreeEntry[] };

function buildTree(nodes: readonly PlanNode[]): TreeEntry[] {
  const byId = new Map<string, TreeEntry>();
  for (const node of nodes) {
    byId.set(node.id, { node, children: [] });
  }
  const roots: TreeEntry[] = [];
  for (const entry of byId.values()) {
    const parentId = entry.node.parentId;
    if (parentId && byId.has(parentId)) {
      byId.get(parentId)!.children.push(entry);
    } else {
      roots.push(entry);
    }
  }
  const sortByOrder = (a: TreeEntry, b: TreeEntry) =>
    a.node.order - b.node.order;
  roots.sort(sortByOrder);
  for (const entry of byId.values()) {
    entry.children.sort(sortByOrder);
  }
  return roots;
}

function PlanNodeRow({ entry, depth }: { entry: TreeEntry; depth: number }) {
  const [expanded, setExpanded] = useState(true);
  const { node, children } = entry;
  const hasChildren = children.length > 0;
  const cost = formatCost(node.estimatedCost);
  return (
    <li>
      <div
        className={cn(
          "rounded-md border border-border bg-card px-2.5 py-1.5",
          node.status === "errored" && "border-destructive/40",
        )}
        style={{ marginLeft: depth * 16 }}
      >
        <div className="flex items-start gap-2">
          {hasChildren ? (
            <button
              type="button"
              onClick={() => setExpanded((v) => !v)}
              className="mt-0.5 inline-flex h-4 w-4 shrink-0 items-center justify-center text-muted-foreground hover:text-foreground"
              aria-label={expanded ? "Collapse step" : "Expand step"}
              aria-expanded={expanded}
            >
              {expanded ? (
                <ChevronDown className="h-3.5 w-3.5" aria-hidden />
              ) : (
                <ChevronRight className="h-3.5 w-3.5" aria-hidden />
              )}
            </button>
          ) : (
            <span className="mt-0.5 inline-flex h-4 w-4 shrink-0" aria-hidden />
          )}
          <PlanStatusIcon status={node.status} />
          <div className="flex-1 space-y-0.5">
            <p className="text-sm leading-tight">{node.description}</p>
            {node.rationale && (
              <p className="text-xs text-muted-foreground">{node.rationale}</p>
            )}
            {cost && (
              <p className="text-[11px] font-mono text-muted-foreground">{cost}</p>
            )}
          </div>
        </div>
      </div>
      {hasChildren && expanded && (
        <ol className="mt-1.5 space-y-1.5">
          {children.map((child) => (
            <PlanNodeRow key={child.node.id} entry={child} depth={depth + 1} />
          ))}
        </ol>
      )}
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

function formatCost(cost: Record<string, unknown>): string | null {
  const tokens = cost.tokens;
  if (typeof tokens === "number") {
    return `~${tokens.toLocaleString()} tokens`;
  }
  const entries = Object.entries(cost).filter(
    ([, v]) => v !== null && v !== undefined,
  );
  if (entries.length === 0) return null;
  return entries.map(([k, v]) => `${k}: ${String(v)}`).join(" · ");
}
