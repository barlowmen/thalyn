import { Activity } from "lucide-react";

/**
 * Inspector placeholder — agent tiles, plan tree, action log, and
 * drift indicator will live here. Empty state for v0.2; the agent
 * runtime arrives in subsequent iterations.
 */
export function InspectorPanel() {
  return (
    <aside
      aria-label="Inspector"
      className="flex h-full flex-col gap-4 bg-surface px-4 py-4"
    >
      <header>
        <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          Inspector
        </h2>
      </header>

      <div className="flex flex-1 flex-col items-center justify-center gap-3 rounded-md border border-dashed border-border px-4 py-8 text-center">
        <Activity
          className="h-8 w-8 text-muted-foreground"
          aria-hidden
        />
        <div>
          <p className="text-sm">No agents running.</p>
          <p className="text-xs text-muted-foreground">
            Agent tiles, plan trees, and the action log will appear
            here once a run is dispatched.
          </p>
        </div>
      </div>
    </aside>
  );
}
