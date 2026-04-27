import { useCallback, useState } from "react";

import { BrowserSurface } from "@/components/browser/browser-surface";
import { ChatSurface } from "@/components/chat/chat-surface";
import { EditorSurface } from "@/components/editor/editor-surface";
import { AppShell } from "@/components/shell/app-shell";
import { SubAgentDetail } from "@/components/subagent/subagent-detail";
import { TerminalSurface } from "@/components/terminal/terminal-surface";

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
            <SubAgentDetail
              runId={openSubAgentRunId}
              onClose={handleCloseSubAgent}
              onTakeOver={handleTakeOver}
            />
          );
        }
        if (activeRail === "editor") {
          return <EditorSurface />;
        }
        if (activeRail === "terminal") {
          return <TerminalSurface />;
        }
        if (activeRail === "browser") {
          return <BrowserSurface />;
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
