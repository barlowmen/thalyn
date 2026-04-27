import { X } from "lucide-react";

import { Button } from "@/components/ui/button";

/**
 * Per-surface close affordance — sits in the right side of every
 * surface header so the user has an explicit "dismiss" instead of
 * having to know that the chat rail icon collapses the surface.
 *
 * Surfaces own their own headers (the title + icon + any
 * surface-specific controls); this is the one piece of chrome
 * every surface shares, factored out so they look identical.
 */
export function SurfaceCloseButton({ onClose }: { onClose?: () => void }) {
  if (!onClose) return null;
  return (
    <Button
      type="button"
      variant="ghost"
      size="icon"
      aria-label="Close surface"
      onClick={onClose}
    >
      <X aria-hidden className="size-4" />
    </Button>
  );
}
