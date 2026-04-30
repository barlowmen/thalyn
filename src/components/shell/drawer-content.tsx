import { lazy, Suspense } from "react";

import type { DrawerKind, DrawerParams } from "@/components/shell/drawer-host";
import type { DrawerEscapeHatch } from "@/components/shell/drawer-surface";

// Heavy surfaces are split out of the chat bundle. The drawer host
// only mounts a kind once the user (or brain) opens it, but the
// dynamic import still keeps Monaco / xterm out of the cold-start
// path until the first open. Each lazy() becomes its own chunk.
const EditorPane = lazy(() =>
  import("@/components/editor/editor-pane").then((m) => ({
    default: m.EditorPane,
  })),
);
const TerminalPane = lazy(() =>
  import("@/components/terminal/terminal-pane").then((m) => ({
    default: m.TerminalPane,
  })),
);
const EmailSurface = lazy(() =>
  import("@/components/email/email-surface").then((m) => ({
    default: m.EmailSurface,
  })),
);
const FileTreeSurface = lazy(() =>
  import("@/components/file-tree/file-tree-surface").then((m) => ({
    default: m.FileTreeSurface,
  })),
);
const ConnectorsSurface = lazy(() =>
  import("@/components/connectors/connectors-surface").then((m) => ({
    default: m.ConnectorsSurface,
  })),
);
const LogsSurface = lazy(() =>
  import("@/components/logs/logs-surface").then((m) => ({
    default: m.LogsSurface,
  })),
);
const BrowserSurface = lazy(() =>
  import("@/components/browser/browser-surface").then((m) => ({
    default: m.BrowserSurface,
  })),
);
const WorkerSurface = lazy(() =>
  import("@/components/worker/worker-surface").then((m) => ({
    default: m.WorkerSurface,
  })),
);
const LeadSurface = lazy(() =>
  import("@/components/lead/lead-surface").then((m) => ({
    default: m.LeadSurface,
  })),
);
const LeadChatSurface = lazy(() =>
  import("@/components/lead/lead-chat-surface").then((m) => ({
    default: m.LeadChatSurface,
  })),
);

function Fallback({ label }: { label: string }) {
  return (
    <div
      role="status"
      aria-live="polite"
      className="flex h-full items-center justify-center text-xs text-muted-foreground"
    >
      Loading {label}…
    </div>
  );
}

/**
 * Map a drawer kind to the surface component that renders inside it.
 * The drawer host calls this once per mounted kind; ``params`` is
 * passed through so brain-opened drawers can target a path / cwd /
 * runId on the underlying tool.
 */
export function resolveDrawer<K extends DrawerKind>(
  kind: K,
  params: DrawerParams[K] | undefined,
) {
  switch (kind) {
    case "editor":
      return (
        <Suspense fallback={<Fallback label="editor" />}>
          <EditorPane />
        </Suspense>
      );
    case "terminal": {
      const termParams = (params ?? {}) as DrawerParams["terminal"];
      return (
        <Suspense fallback={<Fallback label="terminal" />}>
          <TerminalPane cwd={termParams.cwd} />
        </Suspense>
      );
    }
    case "email":
      return (
        <Suspense fallback={<Fallback label="email" />}>
          <EmailSurface />
        </Suspense>
      );
    case "file-tree": {
      const fileParams = (params ?? {}) as DrawerParams["file-tree"];
      return (
        <Suspense fallback={<Fallback label="files" />}>
          <FileTreeSurface root={fileParams.root} />
        </Suspense>
      );
    }
    case "connectors":
      return (
        <Suspense fallback={<Fallback label="connectors" />}>
          <ConnectorsSurface />
        </Suspense>
      );
    case "logs":
      return (
        <Suspense fallback={<Fallback label="logs" />}>
          <LogsSurface />
        </Suspense>
      );
    case "browser": {
      const browserParams = (params ?? {}) as DrawerParams["browser"];
      return (
        <Suspense fallback={<Fallback label="browser" />}>
          <BrowserSurface initialUrl={browserParams.url} />
        </Suspense>
      );
    }
    case "worker": {
      const workerParams = (params ?? {}) as DrawerParams["worker"];
      return (
        <Suspense fallback={<Fallback label="worker" />}>
          <WorkerSurface runId={workerParams.runId} />
        </Suspense>
      );
    }
    case "lead": {
      const leadParams = (params ?? {}) as DrawerParams["lead"];
      return (
        <Suspense fallback={<Fallback label="lead" />}>
          <LeadSurface agentId={leadParams.agentId} />
        </Suspense>
      );
    }
    case "lead-chat": {
      const leadChatParams = (params ?? {}) as DrawerParams["lead-chat"];
      return (
        <Suspense fallback={<Fallback label="lead chat" />}>
          <LeadChatSurface
            agentId={leadChatParams.agentId}
            displayName={leadChatParams.displayName}
          />
        </Suspense>
      );
    }
  }
  // The switch is exhaustive over DrawerKind; this is a type
  // assertion for the compiler.
  return null;
}

/**
 * F5.2 escape-hatch resolution. Returns the user-driven "open in
 * system…" / "reveal in finder" affordance for a given kind + params,
 * or ``undefined`` when there's nothing concrete to point at (e.g.
 * an editor drawer with no path, a terminal with no cwd). The
 * returned ``onClick`` calls the matching Tauri command — wiring
 * lives behind ``invoke`` so storybook / playwright tolerate its
 * absence.
 */
export function resolveEscapeHatch<K extends DrawerKind>(
  kind: K,
  params: DrawerParams[K] | undefined,
): DrawerEscapeHatch | undefined {
  switch (kind) {
    case "editor": {
      const p = (params ?? {}) as DrawerParams["editor"];
      if (!p.path) return undefined;
      return {
        label: "Reveal in Finder",
        onClick: () => invokeReveal(p.path!),
      };
    }
    case "file-tree": {
      const p = (params ?? {}) as DrawerParams["file-tree"];
      if (!p.root) return undefined;
      return {
        label: "Reveal in Finder",
        onClick: () => invokeReveal(p.root!),
      };
    }
    case "terminal": {
      const p = (params ?? {}) as DrawerParams["terminal"];
      if (!p.cwd) return undefined;
      return {
        label: "Open in system terminal",
        onClick: () => invokeReveal(p.cwd!),
      };
    }
    default:
      return undefined;
  }
}

// The reveal helper is best-effort: we route through Tauri's invoke
// when it's available and silently no-op otherwise. Wiring the actual
// Rust command lands when the worker project mount adds real paths
// for the escape hatch to point at.
function invokeReveal(target: string): void {
  void import("@tauri-apps/api/core")
    .then(({ invoke }) => invoke("reveal_in_finder", { target }))
    .catch(() => {
      // No Tauri bridge or no command registered yet. The escape
      // hatch is purely user-driven and the command lands later;
      // the failure is silent so storybook / playwright stay green.
    });
}
