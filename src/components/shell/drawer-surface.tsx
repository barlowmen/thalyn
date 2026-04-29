import { ExternalLink, X } from "lucide-react";
import type { ReactNode } from "react";

import type { DrawerKind } from "@/components/shell/drawer-host";
import { Button } from "@/components/ui/button";

const DRAWER_LABEL: Record<DrawerKind, string> = {
  editor: "Editor",
  terminal: "Terminal",
  email: "Email",
  "file-tree": "Files",
  connectors: "Connectors",
  logs: "Logs",
};

export type DrawerEscapeHatch = {
  /** Short button label, e.g. "Reveal in Finder", "Open in system terminal". */
  label: string;
  onClick: () => void;
};

/**
 * Drawer chrome around a surface — a floating action cluster in the
 * top-right corner with the F5.2 "open in system / reveal in finder"
 * escape hatch (when applicable) and the dismiss button. The drag
 * handle for resize sits on the band's left edge (drawer host owns
 * it); inside the drawer the chrome is purely additive so each
 * surface's existing header stays unchanged.
 *
 * Click-outside to dismiss is replaced by the explicit close button
 * + ⌘\\ shortcut + Cmd-K → "Close drawer" — see ADR-0026 for the
 * trade-off (a click in the chat region was deemed too easy to
 * trigger by accident given drawers and chat coexist horizontally
 * rather than overlay).
 */
export function DrawerSurface({
  kind,
  onClose,
  escapeHatch,
  children,
}: {
  kind: DrawerKind;
  onClose: () => void;
  escapeHatch?: DrawerEscapeHatch;
  children: ReactNode;
}) {
  const label = DRAWER_LABEL[kind];
  return (
    <section
      aria-label={`${label} drawer`}
      className="relative flex h-full min-h-0 min-w-0 flex-1 flex-col bg-background"
    >
      <div className="absolute right-1.5 top-1.5 z-10 flex items-center gap-1">
        {escapeHatch && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={escapeHatch.onClick}
            className="h-7 gap-1 px-2 text-[11px] text-muted-foreground"
          >
            <ExternalLink aria-hidden className="h-3 w-3" />
            <span>{escapeHatch.label}</span>
          </Button>
        )}
        <Button
          type="button"
          variant="ghost"
          size="icon"
          onClick={onClose}
          aria-label={`Close ${label} drawer`}
          className="h-7 w-7"
        >
          <X aria-hidden className="h-4 w-4" />
        </Button>
      </div>
      <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
        {children}
      </div>
    </section>
  );
}
