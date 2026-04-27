import { lazy, Suspense } from "react";

import { Terminal as TerminalIcon } from "lucide-react";

import { SurfaceCloseButton } from "@/components/shell/surface-close";

const TerminalPane = lazy(async () =>
  import("@/components/terminal/terminal-pane").then((mod) => ({
    default: mod.TerminalPane,
  })),
);

/**
 * Main-panel terminal surface. The xterm.js mount loads lazily so
 * the chat shell doesn't pay the bundle cost up front.
 */
export function TerminalSurface({ onClose }: { onClose?: () => void }) {
  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center justify-between border-b border-border bg-background px-6 py-3">
        <div className="flex items-center gap-2">
          <TerminalIcon className="h-4 w-4 text-muted-foreground" aria-hidden />
          <h2 className="text-sm font-semibold tracking-tight">Terminal</h2>
        </div>
        <div className="flex items-center gap-2">
          <p className="text-[11px] text-muted-foreground">
            Local shell · agent-attach lands with the next commit.
          </p>
          <SurfaceCloseButton onClose={onClose} />
        </div>
      </header>
      <div className="flex-1 overflow-hidden">
        <Suspense fallback={<TerminalFallback />}>
          <TerminalPane />
        </Suspense>
      </div>
    </div>
  );
}

function TerminalFallback() {
  return (
    <div className="flex h-full items-center justify-center">
      <p className="text-xs text-muted-foreground">Loading terminal…</p>
    </div>
  );
}
