import { ChevronRight, Pencil, ThumbsUp, X } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { approvePlan, type Plan } from "@/lib/runs";
import { cn } from "@/lib/utils";

/**
 * Inline approval gate (F8.9). Renders inside the chat conversation
 * itself rather than as a modal — the modal is reserved for
 * irreversible actions, while everyday plan-review fits the inline
 * pattern: read the plan, approve in place. The "Edit plan" path
 * still opens the modal because the multi-step editor is too tall
 * for the inline card.
 *
 * The brain's approve/reject path is idempotent: a duplicate click
 * after the gate has been resolved (e.g. via the worker drawer's
 * inline Approve button) is harmless — the second resolve no-ops on
 * the brain side and the card disappears from chat the moment the
 * approval gate clears in the renderer.
 */
export function InlineApprovalCard({
  runId,
  providerId,
  plan,
  onSettled,
  onOpenEditor,
}: {
  runId: string;
  providerId: string;
  plan: Plan;
  /** Called after a successful approve/reject submission so the
   *  parent can clear its gate state and unmount the card. */
  onSettled: () => void;
  /** Switch to the edit modal — the parent owns the modal so this
   *  is a presentational hand-off. */
  onOpenEditor: () => void;
}) {
  const [pending, setPending] = useState<
    | { kind: "idle" }
    | { kind: "submitting" }
    | { kind: "error"; message: string }
  >({ kind: "idle" });
  const [expanded, setExpanded] = useState(plan.nodes.length <= 5);

  const submit = async (decision: "approve" | "reject") => {
    setPending({ kind: "submitting" });
    try {
      await approvePlan({ runId, providerId, decision });
      onSettled();
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setPending({ kind: "error", message });
    }
  };

  const isSubmitting = pending.kind === "submitting";

  return (
    <div
      role="region"
      aria-label="Plan approval"
      className="rounded-lg border border-warning/50 bg-warning/10 px-4 py-3"
    >
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1 space-y-2">
          <div className="space-y-1">
            <p className="text-sm font-medium">Plan ready for review</p>
            {plan.goal && (
              <p className="text-xs text-muted-foreground">
                Goal: <span className="text-foreground">{plan.goal}</span>
              </p>
            )}
          </div>

          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            className="flex items-center gap-1 text-[11px] font-medium text-muted-foreground hover:text-foreground"
            aria-expanded={expanded}
          >
            <ChevronRight
              aria-hidden
              className={cn(
                "size-3 transition-transform motion-reduce:transition-none",
                expanded && "rotate-90",
              )}
            />
            {expanded ? "Hide steps" : `Show ${plan.nodes.length} step${plan.nodes.length === 1 ? "" : "s"}`}
          </button>

          {expanded && (
            <ol className="space-y-1 pl-1">
              {plan.nodes.map((node, idx) => (
                <li key={node.id} className="text-xs leading-snug">
                  <span className="mr-1.5 text-muted-foreground">
                    {idx + 1}.
                  </span>
                  {node.description}
                </li>
              ))}
            </ol>
          )}

          {pending.kind === "error" && (
            <p className="text-xs text-danger">{pending.message}</p>
          )}

          <div className="flex flex-wrap items-center gap-2 pt-1">
            <Button
              type="button"
              size="sm"
              onClick={() => void submit("approve")}
              disabled={isSubmitting}
            >
              <ThumbsUp aria-hidden /> Approve
            </Button>
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={onOpenEditor}
              disabled={isSubmitting}
            >
              <Pencil aria-hidden /> Edit plan
            </Button>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              onClick={() => void submit("reject")}
              disabled={isSubmitting}
            >
              <X aria-hidden /> Reject
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
