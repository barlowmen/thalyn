import { MessageSquare, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { LeadEscalationEvent } from "@/lib/leads";

/**
 * Inline "Lead-X has N open questions — drop into a quick chat?" CTA
 * (F2.5). Renders inside the chat scroll region the same way the
 * inline approval card does so the user sees it where they're
 * already looking. The card has two affordances — drop into the
 * direct-chat drawer, or dismiss the CTA and stay in the eternal
 * thread — both of which the parent wires through ``onAccept`` /
 * ``onDismiss`` props.
 */
export function EscalationCard({
  signal,
  displayName,
  onAccept,
  onDismiss,
}: {
  signal: LeadEscalationEvent;
  /** Optional human-readable name for the lead. The brain only carries
   *  ``leadId`` on the wire; the chat-first shell joins it against
   *  the active lead list before rendering. */
  displayName?: string;
  onAccept: () => void;
  onDismiss: () => void;
}) {
  const label = displayName ?? signal.leadId;
  return (
    <div
      role="region"
      aria-label="Lead escalation suggestion"
      className="flex items-start gap-3 rounded-lg border border-primary/30 bg-primary/5 px-4 py-3"
    >
      <MessageSquare
        aria-hidden
        className="mt-0.5 size-4 shrink-0 text-primary"
      />
      <div className="min-w-0 flex-1 space-y-2">
        <p className="text-sm">
          <span className="font-medium">{label}</span> has{" "}
          {signal.questionCount} open question
          {signal.questionCount === 1 ? "" : "s"} — want to drop into a
          quick chat, or have me walk through them one by one?
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            type="button"
            size="sm"
            onClick={onAccept}
          >
            <MessageSquare aria-hidden /> Drop into {label}
          </Button>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            onClick={onDismiss}
          >
            <X aria-hidden /> Stay here
          </Button>
        </div>
      </div>
    </div>
  );
}
