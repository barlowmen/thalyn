import { Suspense, lazy, useCallback, useState } from "react";

import { ChatSurface } from "@/components/chat/chat-surface";
import { FirstRunWizard } from "@/components/onboarding/first-run-wizard";
import { ChatFirstShell } from "@/components/shell/chat-first-shell";
import { AppShell } from "@/components/shell/app-shell";
import { usePathname } from "@/lib/use-pathname";

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
  const pathname = usePathname();
  // The chat-first shell is the default route. The legacy mosaic
  // shell remains reachable under ``/legacy`` while the drawer-host
  // primitive comes online; once drawers re-home the surfaces, the
  // legacy route and the mosaic shell delete together (ADR-0026).
  const isLegacy = pathname === "/legacy" || pathname.startsWith("/legacy/");

  return (
    <>
      <FirstRunWizard />
      {isLegacy ? <LegacyShell /> : <ChatFirstShell />}
    </>
  );
}

function LegacyShell() {
  const [openSubAgentRunId, setOpenSubAgentRunId] = useState<string | null>(null);
  const [takeOverRunId, setTakeOverRunId] = useState<string | null>(null);

  const handleOpenSubAgent = useCallback((runId: string) => {
    setOpenSubAgentRunId(runId);
  }, []);
  const handleCloseSubAgent = useCallback(() => {
    setOpenSubAgentRunId(null);
  }, []);
  // Take-over keeps the sub-agent detail open in the surface region
  // alongside the take-over chat in the chat region — that side-by-side
  // is the whole point of having two regions.
  const handleTakeOver = useCallback((runId: string) => {
    setTakeOverRunId(runId);
  }, []);
  const handleHandBack = useCallback(() => {
    setTakeOverRunId(null);
  }, []);

  return (
    <AppShell
      openSubAgentRunId={openSubAgentRunId}
      onOpenSubAgent={handleOpenSubAgent}
      onCloseSurface={handleCloseSubAgent}
      surface={({ activeRail, closeSurface }) => {
        // Sub-agent detail wins over the rail's surface — it's the
        // result of an explicit "open this agent" click, not a tab
        // switch.
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
              <EditorSurface onClose={closeSurface} />
            </Suspense>
          );
        }
        if (activeRail === "terminal") {
          return (
            <Suspense fallback={<SurfaceFallback label="terminal" />}>
              <TerminalSurface onClose={closeSurface} />
            </Suspense>
          );
        }
        if (activeRail === "browser") {
          return (
            <Suspense fallback={<SurfaceFallback label="browser" />}>
              <BrowserSurface onClose={closeSurface} />
            </Suspense>
          );
        }
        if (activeRail === "email") {
          return (
            <Suspense fallback={<SurfaceFallback label="email" />}>
              <EmailSurface onClose={closeSurface} />
            </Suspense>
          );
        }
        if (activeRail === "agents") {
          return (
            <Suspense fallback={<SurfaceFallback label="agents" />}>
              <AgentsSurface onOpen={handleOpenSubAgent} onClose={closeSurface} />
            </Suspense>
          );
        }
        if (activeRail === "logs") {
          return (
            <Suspense fallback={<SurfaceFallback label="logs" />}>
              <LogsSurface onOpen={handleOpenSubAgent} onClose={closeSurface} />
            </Suspense>
          );
        }
        if (activeRail === "connectors") {
          return (
            <Suspense fallback={<SurfaceFallback label="connectors" />}>
              <ConnectorsSurface onClose={closeSurface} />
            </Suspense>
          );
        }
        // activeRail === "chat" — nothing to render; the shell will
        // collapse the surface panel on its own.
        return null;
      }}
      chat={({ openSettings }) => (
        <ChatSurface
          // Remount on takeover so the chat session, message list,
          // and system prompt all reset cleanly.
          key={takeOverRunId ?? "main"}
          onOpenSettings={openSettings}
          onOpenSubAgent={handleOpenSubAgent}
          takeOverRunId={takeOverRunId}
          onHandBack={handleHandBack}
        />
      )}
    />
  );
}

export default App;
