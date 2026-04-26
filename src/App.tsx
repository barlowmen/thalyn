import { useCallback, useState } from "react";

import { ChatSurface } from "@/components/chat/chat-surface";
import { AppShell } from "@/components/shell/app-shell";
import { SubAgentDetail } from "@/components/subagent/subagent-detail";

function App() {
  const [openSubAgentRunId, setOpenSubAgentRunId] = useState<string | null>(null);

  const handleOpenSubAgent = useCallback((runId: string) => {
    setOpenSubAgentRunId(runId);
  }, []);
  const handleCloseSubAgent = useCallback(() => {
    setOpenSubAgentRunId(null);
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
          />
        ) : (
          <ChatSurface
            onOpenSettings={openSettings}
            onOpenSubAgent={handleOpenSubAgent}
          />
        )
      }
    />
  );
}

export default App;
