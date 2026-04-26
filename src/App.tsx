import { ChatSurface } from "@/components/chat/chat-surface";
import { AppShell } from "@/components/shell/app-shell";

function App() {
  return (
    <AppShell
      main={({ openSettings }) => (
        <ChatSurface onOpenSettings={openSettings} />
      )}
    />
  );
}

export default App;
