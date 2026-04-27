import { Fragment, useCallback, useEffect, useState } from "react";

import { Compass, ExternalLink, Loader2, Power, Square } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  type BrowserState,
  browserStateLabel,
  getBrowserStatus,
  startBrowser,
  stopBrowser,
} from "@/lib/browser";

/**
 * Browser surface — the observability + intervention console for the
 * headed Chromium sidecar. The real Chromium window is the user-facing
 * browser (logins, downloads, IME all just work natively); this panel
 * shows status, lifecycle controls, and — once an agent is driving —
 * what the agent is up to.
 *
 * For this commit we ship the lifecycle skeleton: status display,
 * Start / Stop, the WS endpoint and profile dir for debuggability.
 * Take-over (window-raise) and the screencast preview / action-log
 * overlay land alongside the per-step capture commit.
 *
 * The component is split into a connected wrapper (this) and a
 * presentational [`BrowserView`] so Storybook can render every state
 * without touching the Tauri invoke surface.
 */
export function BrowserSurface() {
  const [state, setState] = useState<BrowserState>({ kind: "idle" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const next = await getBrowserStatus();
      setState(next);
    } catch (err) {
      setError(messageOf(err));
    }
  }, []);

  useEffect(() => {
    void refresh();
    // The state-watch broadcast lands with the screencast commit; in
    // the meantime a low-rate poll keeps the panel in sync if the
    // user starts / stops from elsewhere.
    const id = window.setInterval(refresh, 2_000);
    return () => window.clearInterval(id);
  }, [refresh]);

  const handleStart = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const next = await startBrowser();
      setState(next);
    } catch (err) {
      setError(messageOf(err));
    } finally {
      setBusy(false);
    }
  }, []);

  const handleStop = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      await stopBrowser();
      await refresh();
    } catch (err) {
      setError(messageOf(err));
    } finally {
      setBusy(false);
    }
  }, [refresh]);

  return (
    <BrowserView
      state={state}
      busy={busy}
      error={error}
      onStart={handleStart}
      onStop={handleStop}
    />
  );
}

/**
 * Presentational shape of the browser surface. Renders the same
 * UI that [`BrowserSurface`] does but takes everything via props so
 * Storybook (and unit tests) can drive it without a Tauri host.
 */
export function BrowserView({
  state,
  busy,
  error,
  onStart,
  onStop,
}: {
  state: BrowserState;
  busy: boolean;
  error: string | null;
  onStart: () => void;
  onStop: () => void;
}) {
  const isRunning = state.kind === "running";
  const isStarting = state.kind === "starting" || busy;

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center justify-between border-b border-border bg-background px-6 py-3">
        <div className="flex items-center gap-2">
          <Compass className="h-4 w-4 text-muted-foreground" aria-hidden />
          <h2 className="text-sm font-semibold tracking-tight">Browser</h2>
          <Badge tone={badgeToneFor(state)}>{browserStateLabel(state)}</Badge>
        </div>
        <p className="text-[11px] text-muted-foreground">
          Real Chromium window · the panel is observability only
        </p>
      </header>

      <div className="flex flex-1 flex-col gap-4 overflow-y-auto p-6">
        <ControlBar
          state={state}
          busy={busy}
          isStarting={isStarting}
          isRunning={isRunning}
          onStart={onStart}
          onStop={onStop}
        />

        {error ? (
          <div
            role="alert"
            className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-foreground"
          >
            {error}
          </div>
        ) : null}

        <BrowserDetails state={state} />

        {state.kind === "idle" ? <EmptyState /> : null}
      </div>
    </div>
  );
}

function ControlBar({
  state,
  busy,
  isStarting,
  isRunning,
  onStart,
  onStop,
}: {
  state: BrowserState;
  busy: boolean;
  isStarting: boolean;
  isRunning: boolean;
  onStart: () => void;
  onStop: () => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Button
        type="button"
        size="sm"
        onClick={onStart}
        disabled={isRunning || busy || state.kind === "starting"}
        aria-label={isStarting ? "Starting browser" : "Start browser"}
      >
        {isStarting ? (
          <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden />
        ) : (
          <Power className="mr-2 h-4 w-4" aria-hidden />
        )}
        {isStarting ? "Starting…" : "Start browser"}
      </Button>
      <Button
        type="button"
        size="sm"
        variant="outline"
        onClick={onStop}
        disabled={!isRunning || busy}
      >
        <Square className="mr-2 h-4 w-4" aria-hidden />
        Stop
      </Button>
    </div>
  );
}

function BrowserDetails({ state }: { state: BrowserState }) {
  if (state.kind === "starting") {
    return (
      <DetailGrid
        rows={[
          { label: "Binary", value: state.binary },
          { label: "Profile", value: "preparing…" },
          { label: "DevTools", value: "waiting for port…" },
        ]}
      />
    );
  }
  if (state.kind === "exited") {
    return <DetailGrid rows={[{ label: "Last run", value: state.reason }]} />;
  }
  if (state.kind === "running") {
    return (
      <DetailGrid
        rows={[
          { label: "Binary", value: state.binary },
          { label: "Profile", value: state.profile_dir },
          { label: "DevTools", value: state.ws_url, mono: true },
        ]}
      />
    );
  }
  return null;
}

function DetailGrid({
  rows,
}: {
  rows: { label: string; value: string; mono?: boolean }[];
}) {
  // Direct dt/dd children of dl — wrapping in a <div> would break
  // axe's `definition-list` rule. Fragment keeps the React key
  // bookkeeping while letting the dl/dt/dd markup stay valid.
  return (
    <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-xs">
      {rows.map(({ label, value, mono }) => (
        <Fragment key={label}>
          <dt className="text-muted-foreground">{label}</dt>
          <dd
            className={
              mono
                ? "font-mono text-foreground/90 break-all"
                : "text-foreground/90 break-all"
            }
          >
            {value}
          </dd>
        </Fragment>
      ))}
    </dl>
  );
}

function EmptyState() {
  return (
    <div className="flex flex-col gap-2 rounded-lg border border-dashed border-border p-6 text-sm">
      <h3 className="text-sm font-medium">No browser running</h3>
      <p className="text-muted-foreground">
        Start a Chromium session to let agents browse and act. The Chromium
        window is a real browser you can also click in directly — log in,
        approve a 2FA prompt, drag-drop a file, all the things screencasted
        previews can&rsquo;t do.
      </p>
      <p className="flex items-center gap-1 text-[11px] text-muted-foreground">
        <ExternalLink className="h-3 w-3" aria-hidden />
        Uses your installed Chrome / Chromium / Edge / Brave (no download).
      </p>
    </div>
  );
}

function badgeToneFor(
  state: BrowserState,
): "default" | "success" | "warning" | "danger" | "muted" {
  switch (state.kind) {
    case "running":
      return "success";
    case "starting":
      return "warning";
    case "exited":
      return "danger";
    case "idle":
      return "muted";
  }
}

function messageOf(err: unknown): string {
  if (err instanceof Error) return err.message;
  if (typeof err === "string") return err;
  return JSON.stringify(err);
}
