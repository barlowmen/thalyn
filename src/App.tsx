import { useCallback, useState } from "react";

import { ChatSurface } from "@/components/chat/chat-surface";
import { AppShell } from "@/components/shell/app-shell";
import { SubAgentDetail } from "@/components/subagent/subagent-detail";

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
      main={({ openSettings }) =>
        openSubAgentRunId ? (
          <SubAgentDetail
            runId={openSubAgentRunId}
            onClose={handleCloseSubAgent}
            onTakeOver={handleTakeOver}
          />
        ) : (
          <ChatSurface
            // Remount on takeover so the chat session, message list,
            // and system prompt all reset cleanly.
            key={takeOverRunId ?? "main"}
            onOpenSettings={openSettings}
            onOpenSubAgent={handleOpenSubAgent}
            takeOverRunId={takeOverRunId}
            onHandBack={handleHandBack}
          />
        )
      }
    />
  );
}

export default App;
