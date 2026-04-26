import { invoke } from "@tauri-apps/api/core";
import { useState } from "react";

import { Button } from "@/components/ui/button";

type PongPayload = {
  pong: boolean;
  version: string;
  epoch_ms: number;
};

type PingState =
  | { kind: "idle" }
  | { kind: "pending" }
  | { kind: "ok"; pong: PongPayload; latencyMs: number }
  | { kind: "error"; message: string };

function App() {
  const [state, setState] = useState<PingState>({ kind: "idle" });

  const ping = async () => {
    setState({ kind: "pending" });
    const start = performance.now();
    try {
      const pong = await invoke<PongPayload>("ping_brain");
      const latencyMs = Math.round(performance.now() - start);
      setState({ kind: "ok", pong, latencyMs });
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setState({ kind: "error", message });
    }
  };

  return (
    <main className="flex min-h-screen flex-col items-center gap-4 px-6 pt-[12vh] text-center">
      <h1 className="text-3xl font-semibold tracking-tight">Thalyn</h1>
      <p className="text-muted-foreground">
        Walking skeleton &mdash; ping the brain.
      </p>

      <Button
        onClick={ping}
        disabled={state.kind === "pending"}
        aria-busy={state.kind === "pending"}
      >
        {state.kind === "pending" ? "Pinging…" : "Ping brain"}
      </Button>

      <div
        className="min-h-6 text-sm"
        role="status"
        aria-live="polite"
      >
        <PingStatus state={state} />
      </div>
    </main>
  );
}

function PingStatus({ state }: { state: PingState }) {
  switch (state.kind) {
    case "idle":
      return (
        <span className="text-muted-foreground">No request issued yet.</span>
      );
    case "pending":
      return (
        <span className="text-muted-foreground">
          Waiting for the sidecar…
        </span>
      );
    case "ok":
      return (
        <span>
          Pong from brain v{state.pong.version} in {state.latencyMs} ms (epoch{" "}
          {state.pong.epoch_ms}).
        </span>
      );
    case "error":
      return <span className="text-destructive">{state.message}</span>;
  }
}

export default App;
