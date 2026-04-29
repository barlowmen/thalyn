import { useEffect, useState } from "react";

import {
  type LeadEscalationEvent,
  subscribeLeadEscalation,
} from "@/lib/leads";

/**
 * Track the most recent F2.5 escalation hint from the brain. The
 * renderer surfaces an inline "drop into Lead-X" CTA whenever the
 * value is non-null; clicking through opens the lead-chat drawer and
 * the consumer calls ``dismiss()`` so the CTA clears.
 *
 * The hook stores only the latest hint — escalations are advisory
 * and a fresh signal supersedes the previous one rather than
 * stacking. Storybook / playwright tolerate the missing Tauri bridge
 * the same way every other listener in the codebase does.
 */
export function useLeadEscalation(): {
  signal: LeadEscalationEvent | null;
  dismiss: () => void;
} {
  const [signal, setSignal] = useState<LeadEscalationEvent | null>(null);

  useEffect(() => {
    let active = true;
    let unlisten: (() => void) | undefined;
    subscribeLeadEscalation((event) => {
      if (!active) return;
      // The brain only fires when the suggestion is "open_drawer", so
      // any payload reaching us is high-density and worth surfacing.
      setSignal(event);
    })
      .then((fn) => {
        if (!active) {
          fn();
          return;
        }
        unlisten = fn;
      })
      .catch(() => {
        // No-op outside Tauri — storybook / playwright stay green.
      });
    return () => {
      active = false;
      unlisten?.();
    };
  }, []);

  return { signal, dismiss: () => setSignal(null) };
}
