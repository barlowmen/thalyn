import { Pencil, ThumbsUp, X } from "lucide-react";
import { type FormEvent, useEffect, useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { approvePlan, type Plan, type PlanNode } from "@/lib/runs";
import { cn } from "@/lib/utils";

type Pending = { kind: "idle" } | { kind: "submitting" } | { kind: "error"; message: string };

type Props = {
  open: boolean;
  runId: string | null;
  providerId: string;
  plan: Plan | null;
  onSettled: () => void;
};

/**
 * Plan-approval modal — Approve / Edit / Reject. Per F11.6 the plan
 * must be readable in under 30 seconds; nodes render as a checklist
 * with rationale; edit mode lets the user revise descriptions and
 * rationale inline before re-submitting.
 */
export function PlanApprovalDialog({
  open,
  runId,
  providerId,
  plan,
  onSettled,
}: Props) {
  const [mode, setMode] = useState<"review" | "edit">("review");
  const [draft, setDraft] = useState<Plan | null>(plan);
  const [pending, setPending] = useState<Pending>({ kind: "idle" });

  // Reset draft + mode when a new plan arrives.
  useEffect(() => {
    setDraft(plan);
    setMode("review");
    setPending({ kind: "idle" });
  }, [plan, runId]);

  const submit = async (decision: "approve" | "edit" | "reject") => {
    if (!runId) return;
    setPending({ kind: "submitting" });
    try {
      await approvePlan({
        runId,
        providerId,
        decision,
        editedPlan: decision === "edit" ? draft ?? undefined : undefined,
      });
      onSettled();
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setPending({ kind: "error", message });
    }
  };

  const onApprove = () => submit("approve");
  const onReject = () => submit("reject");
  const onSubmitEdits = (event: FormEvent) => {
    event.preventDefault();
    submit("edit");
  };

  const isSubmitting = pending.kind === "submitting";

  return (
    <Dialog open={open} onOpenChange={(next) => !next && onSettled()}>
      <DialogContent className="max-h-[80vh] overflow-y-auto sm:max-w-[640px]">
        <header className="space-y-1">
          <DialogTitle>Plan ready for review</DialogTitle>
          <DialogDescription>
            The brain produced this plan from your prompt. Approve to
            run it, edit the steps inline, or reject to start over.
          </DialogDescription>
        </header>

        {plan === null ? (
          <p className="text-sm text-muted-foreground">No plan to review.</p>
        ) : mode === "review" ? (
          <PlanReview plan={plan} />
        ) : (
          <PlanEditor
            plan={draft ?? plan}
            onChange={setDraft}
            onSubmit={onSubmitEdits}
          />
        )}

        {pending.kind === "error" && (
          <p className="text-sm text-destructive">{pending.message}</p>
        )}

        <footer className="flex flex-wrap items-center justify-end gap-2">
          {mode === "review" ? (
            <>
              <Button
                type="button"
                variant="ghost"
                onClick={onReject}
                disabled={isSubmitting}
              >
                <X aria-hidden /> Reject
              </Button>
              <Button
                type="button"
                variant="outline"
                onClick={() => setMode("edit")}
                disabled={isSubmitting}
              >
                <Pencil aria-hidden /> Edit
              </Button>
              <Button
                type="button"
                onClick={onApprove}
                disabled={isSubmitting}
              >
                <ThumbsUp aria-hidden /> Approve
              </Button>
            </>
          ) : (
            <>
              <Button
                type="button"
                variant="ghost"
                onClick={() => setMode("review")}
                disabled={isSubmitting}
              >
                Cancel
              </Button>
              <Button
                type="button"
                onClick={onSubmitEdits}
                disabled={isSubmitting || !draft || draft.nodes.length === 0}
              >
                <ThumbsUp aria-hidden />
                {isSubmitting ? "Saving…" : "Save and approve"}
              </Button>
            </>
          )}
        </footer>
      </DialogContent>
    </Dialog>
  );
}

function PlanReview({ plan }: { plan: Plan }) {
  return (
    <section className="space-y-3">
      {plan.goal && (
        <div className="rounded-md border border-border bg-card px-3 py-2">
          <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            Goal
          </p>
          <p className="mt-1 text-sm">{plan.goal}</p>
        </div>
      )}
      <ol className="space-y-2">
        {plan.nodes.map((node, idx) => (
          <li
            key={node.id}
            className="rounded-md border border-border bg-card px-3 py-2"
          >
            <p className="text-sm font-medium">
              <span className="mr-2 text-muted-foreground">{idx + 1}.</span>
              {node.description}
            </p>
            {node.rationale && (
              <p className="mt-1 text-xs text-muted-foreground">
                {node.rationale}
              </p>
            )}
            {Object.keys(node.estimatedCost).length > 0 && (
              <p className="mt-1 text-xs text-muted-foreground">
                {formatCost(node.estimatedCost)}
              </p>
            )}
          </li>
        ))}
      </ol>
    </section>
  );
}

function PlanEditor({
  plan,
  onChange,
  onSubmit,
}: {
  plan: Plan;
  onChange: (plan: Plan) => void;
  onSubmit: (event: FormEvent) => void;
}) {
  const editorPlan = useMemo(() => plan, [plan]);

  const updateNode = (idx: number, patch: Partial<PlanNode>) => {
    const nextNodes = editorPlan.nodes.map((n, i) =>
      i === idx ? { ...n, ...patch } : n,
    );
    onChange({ ...editorPlan, nodes: nextNodes });
  };

  const removeNode = (idx: number) => {
    const next = editorPlan.nodes.filter((_, i) => i !== idx);
    onChange({ ...editorPlan, nodes: next });
  };

  const addNode = () => {
    const next: PlanNode = {
      id: `step_${Date.now()}_${editorPlan.nodes.length}`,
      order: editorPlan.nodes.length,
      description: "",
      rationale: "",
      estimatedCost: {},
      status: "pending",
      parentId: null,
    };
    onChange({ ...editorPlan, nodes: [...editorPlan.nodes, next] });
  };

  return (
    <form className="space-y-3" onSubmit={onSubmit}>
      <div className="space-y-1.5">
        <Label htmlFor="plan-goal">Goal</Label>
        <Input
          id="plan-goal"
          value={editorPlan.goal}
          onChange={(event) =>
            onChange({ ...editorPlan, goal: event.target.value })
          }
        />
      </div>

      <ol className="space-y-2">
        {editorPlan.nodes.map((node, idx) => (
          <li
            key={node.id}
            className={cn(
              "rounded-md border border-border bg-card px-3 py-2 space-y-2",
            )}
          >
            <div className="flex items-center justify-between gap-2">
              <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                Step {idx + 1}
              </span>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => removeNode(idx)}
              >
                Remove
              </Button>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor={`step-desc-${idx}`}>Description</Label>
              <Input
                id={`step-desc-${idx}`}
                value={node.description}
                onChange={(event) =>
                  updateNode(idx, { description: event.target.value })
                }
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor={`step-rationale-${idx}`}>Rationale</Label>
              <Input
                id={`step-rationale-${idx}`}
                value={node.rationale}
                onChange={(event) =>
                  updateNode(idx, { rationale: event.target.value })
                }
              />
            </div>
          </li>
        ))}
      </ol>

      <Button type="button" variant="outline" size="sm" onClick={addNode}>
        Add step
      </Button>
    </form>
  );
}

function formatCost(cost: Record<string, unknown>): string {
  const tokens = cost.tokens;
  if (typeof tokens === "number") {
    return `~${tokens.toLocaleString()} tokens`;
  }
  return Object.entries(cost)
    .map(([key, value]) => `${key}: ${String(value)}`)
    .join(" · ");
}
