import { FolderOpen, Plus } from "lucide-react";

import { Button } from "@/components/ui/button";

/**
 * Sidebar placeholder — projects + recents will live here. The empty
 * state explains what the surface is for and offers a single
 * primary action (per F11.12: empty states with intent).
 */
export function SidebarPanel() {
  return (
    <aside
      aria-label="Sidebar"
      className="flex h-full flex-col gap-4 bg-surface px-4 py-4"
    >
      <header className="flex items-center justify-between">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          Projects
        </h2>
        <Button variant="ghost" size="icon" aria-label="New project">
          <Plus aria-hidden />
        </Button>
      </header>

      <div className="flex flex-1 flex-col items-center justify-center gap-3 rounded-md border border-dashed border-border px-4 py-8 text-center">
        <FolderOpen
          className="h-8 w-8 text-muted-foreground"
          aria-hidden
        />
        <div>
          <p className="text-sm">No projects yet.</p>
          <p className="text-xs text-muted-foreground">
            Open a directory to start.
          </p>
        </div>
        <Button size="sm" variant="outline">
          Open project…
        </Button>
      </div>
    </aside>
  );
}
