import { FolderOpen, RefreshCw } from "lucide-react";

import { Button } from "@/components/ui/button";

/**
 * In-app file browser drawer (F5.8). v0.27 ships the empty state —
 * an explainer plus a single "Open project…" CTA — so the drawer
 * primitive can host a real surface without waiting on the worker
 * project mount that lights this up. The path / line targeting that
 * the brain passes through ``DrawerParams['file-tree']`` is read on
 * mount; once the project mount lands the tree renders here.
 *
 * The "Reveal in Finder" escape hatch is owned by the drawer chrome
 * (DrawerSurface) — wiring it from inside this component would
 * duplicate the affordance in two places.
 */
export function FileTreeSurface({ root }: { root?: string }) {
  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b border-border bg-background px-3 py-2">
        <p className="truncate text-[11px] text-muted-foreground">
          {root ?? "No project mounted"}
        </p>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          aria-label="Refresh file tree"
          className="h-7 w-7"
          disabled
        >
          <RefreshCw aria-hidden className="h-3.5 w-3.5" />
        </Button>
      </div>
      <div className="flex flex-1 flex-col items-center justify-center gap-3 px-6 text-center">
        <FolderOpen
          className="h-8 w-8 text-muted-foreground"
          aria-hidden
        />
        <div>
          <p className="text-sm">No project mounted yet.</p>
          <p className="text-xs text-muted-foreground">
            Ask Thalyn to open a project, or pick one from the project
            switcher.
          </p>
        </div>
        <Button size="sm" variant="outline" disabled>
          Open project…
        </Button>
      </div>
    </div>
  );
}
