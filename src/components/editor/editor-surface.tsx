import { lazy, Suspense } from "react";

import { FileText } from "lucide-react";

import { SurfaceCloseButton } from "@/components/shell/surface-close";

const EditorPane = lazy(async () =>
  import("./editor-pane").then((mod) => ({ default: mod.EditorPane })),
);

/**
 * Main-panel editor surface. Monaco itself is loaded lazily via a
 * dynamic import so the chat shell can paint within budget; the
 * fallback below is what renders during the dynamic import.
 */
export function EditorSurface({ onClose }: { onClose?: () => void }) {
  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center justify-between border-b border-border bg-background px-6 py-3">
        <div className="flex items-center gap-2">
          <FileText className="h-4 w-4 text-muted-foreground" aria-hidden />
          <h2 className="text-sm font-semibold tracking-tight">Editor</h2>
        </div>
        <div className="flex items-center gap-2">
          <p className="text-[11px] text-muted-foreground">
            Scratch buffer · open-file workflow lands with the file tree.
          </p>
          <SurfaceCloseButton onClose={onClose} />
        </div>
      </header>
      <div className="flex-1 overflow-hidden">
        <Suspense fallback={<EditorFallback />}>
          <EditorPane />
        </Suspense>
      </div>
    </div>
  );
}

function EditorFallback() {
  return (
    <div className="flex h-full items-center justify-center">
      <p className="text-xs text-muted-foreground">Loading editor…</p>
    </div>
  );
}
