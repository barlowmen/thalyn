import { Suspense, lazy, useCallback, useState } from "react";

import { ChatSurface } from "@/components/chat/chat-surface";
import { AppShell } from "@/components/shell/app-shell";

// Heavy surfaces are split out of the initial chat bundle so the
// cold-start path stays inside the NFR1 budget. Each lazy import
// becomes its own chunk; the Suspense fallback covers the brief
// load while the chunk hydrates.
const EditorSurface = lazy(() =>
  import("@/components/editor/editor-surface").then((m) => ({
    default: m.EditorSurface,
  })),
);
const TerminalSurface = lazy(() =>
  import("@/components/terminal/terminal-surface").then((m) => ({
    default: m.TerminalSurface,
  })),
);
const BrowserSurface = lazy(() =>
  import("@/components/browser/browser-surface").then((m) => ({
    default: m.BrowserSurface,
  })),
);
const EmailSurface = lazy(() =>
  import("@/components/email/email-surface").then((m) => ({
    default: m.EmailSurface,
  })),
);
const AgentsSurface = lazy(() =>
  import("@/components/agents/agents-surface").then((m) => ({
    default: m.AgentsSurface,
  })),
);
const LogsSurface = lazy(() =>
  import("@/components/logs/logs-surface").then((m) => ({
    default: m.LogsSurface,
  })),
);
const ConnectorsSurface = lazy(() =>
  import("@/components/connectors/connectors-surface").then((m) => ({
    default: m.ConnectorsSurface,
  })),
);
const SubAgentDetail = lazy(() =>
  import("@/components/subagent/subagent-detail").then((m) => ({
    default: m.SubAgentDetail,
  })),
);

function SurfaceFallback({ label }: { label: string }) {
  return (
    <div
      role="status"
      aria-live="polite"
      className="flex h-full items-center justify-center text-sm text-muted-foreground"
    >
      Loading {label}…
    </div>
  );
}

function App() {
  const [openSubAgentRunId, setOpenSubAgentRunId] = useState<string | null>(null);
  const [takeOverRunId, setTakeOverRunId] = useState<string | null>(null);

  const handleOpenSubAgent = useCallback((runId: string) => {
    setOpenSubAgentRunId(runId);
  }, []);
  const handleCloseSubAgent = useCallback(() => {
    setOpenSubAgentRunId(null);
  }, []);
  const handleTakeOver = useCallback((runId: string) => {
    setTakeOverRunId(runId);
    setOpenSubAgentRunId(null);
  }, []);
  const handleHandBack = useCallback(() => {
    setTakeOverRunId(null);
  }, []);

  return (
    <AppShell
      openSubAgentRunId={openSubAgentRunId}
      onOpenSubAgent={handleOpenSubAgent}
      main={({ openSettings, activeRail }) => {
        if (openSubAgentRunId) {
          return (
            <Suspense fallback={<SurfaceFallback label="sub-agent" />}>
              <SubAgentDetail
                runId={openSubAgentRunId}
                onClose={handleCloseSubAgent}
                onTakeOver={handleTakeOver}
              />
            </Suspense>
          );
        }
        if (activeRail === "editor") {
          return (
            <Suspense fallback={<SurfaceFallback label="editor" />}>
              <EditorSurface />
            </Suspense>
          );
        }
        if (activeRail === "terminal") {
          return (
            <Suspense fallback={<SurfaceFallback label="terminal" />}>
              <TerminalSurface />
            </Suspense>
          );
        }
        if (activeRail === "browser") {
          return (
            <Suspense fallback={<SurfaceFallback label="browser" />}>
              <BrowserSurface />
            </Suspense>
          );
        }
        if (activeRail === "email") {
          return (
            <Suspense fallback={<SurfaceFallback label="email" />}>
              <EmailSurface />
            </Suspense>
          );
        }
        if (activeRail === "agents") {
          return (
            <Suspense fallback={<SurfaceFallback label="agents" />}>
              <AgentsSurface onOpen={handleOpenSubAgent} />
            </Suspense>
          );
        }
        if (activeRail === "logs") {
          return (
            <Suspense fallback={<SurfaceFallback label="logs" />}>
              <LogsSurface onOpen={handleOpenSubAgent} />
            </Suspense>
          );
        }
        if (activeRail === "connectors") {
          return (
            <Suspense fallback={<SurfaceFallback label="connectors" />}>
              <ConnectorsSurface />
            </Suspense>
          );
        }
        return (
          <ChatSurface
            // Remount on takeover so the chat session, message list,
            // and system prompt all reset cleanly.
            key={takeOverRunId ?? "main"}
            onOpenSettings={openSettings}
            onOpenSubAgent={handleOpenSubAgent}
            takeOverRunId={takeOverRunId}
            onHandBack={handleHandBack}
          />
        );
      }}
    />
  );
}

export default App;
