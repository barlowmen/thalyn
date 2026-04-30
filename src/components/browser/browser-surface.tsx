import {
  ArrowLeft,
  ArrowRight,
  ExternalLink,
  Globe,
  RefreshCw,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  type BrowserState,
  type BrowserWindowRect,
  browserStateLabel,
  getBrowserStatus,
  setBrowserWindowRect,
  startBrowser,
  stopBrowser,
} from "@/lib/browser";
import { cn } from "@/lib/utils";

/**
 * Browser drawer surface — chrome for the bundled CEF child window.
 *
 * The actual web content renders inside a Chromium window the OS
 * parents over the drawer's content rect (per ADR-0019's v0.29
 * refinement). This component owns the *chrome* — back / forward /
 * URL / reload + the F5.2 "Open in system browser" escape — and the
 * lifecycle control that starts and stops the bundled engine. The
 * placeholder stripe in the body is what the user sees when the
 * Chromium window isn't yet attached (engine starting, exited, or
 * the parenting bridge isn't wired in this build).
 */
export function BrowserSurface({
  initialUrl,
}: {
  initialUrl?: string;
}) {
  const [state, setState] = useState<BrowserState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [url, setUrl] = useState(initialUrl ?? "");

  const refresh = useCallback(async () => {
    try {
      const next = await getBrowserStatus();
      setState(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setState({ kind: "idle" });
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const start = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const next = await startBrowser();
      setState(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }, []);

  const stop = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      await stopBrowser();
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }, [refresh]);

  return (
    <BrowserView
      state={state}
      error={error}
      busy={busy}
      url={url}
      onUrlChange={setUrl}
      onStart={() => void start()}
      onStop={() => void stop()}
      onSubmit={() => {
        // Brain navigates over CDP today; the URL bar's submit is
        // wired to the parented child window in step 7's parenting
        // commit. Here we expose the input so the chrome is testable
        // in isolation.
      }}
      onRectChange={(rect) => void setBrowserWindowRect(rect)}
    />
  );
}

export type BrowserViewProps = {
  state: BrowserState | null;
  error: string | null;
  busy: boolean;
  url: string;
  onUrlChange: (url: string) => void;
  onStart: () => void;
  onStop: () => void;
  onSubmit: () => void;
  /**
   * Fires whenever the drawer's CEF host rect changes (resize,
   * drawer-width drag, parent-window resize). Storybook stories
   * leave it unset so the view stays purely presentational; the
   * connected `BrowserSurface` wires it to `setBrowserWindowRect`
   * so the OS-level parenting layer can keep the bundled CEF child
   * window aligned with the drawer.
   */
  onRectChange?: (rect: BrowserWindowRect) => void;
};

/**
 * View-only layer. Storybook drives this directly so the chrome
 * states are fully testable without the Tauri bridge.
 */
export function BrowserView({
  state,
  error,
  busy,
  url,
  onUrlChange,
  onStart,
  onStop,
  onSubmit,
  onRectChange,
}: BrowserViewProps) {
  const rectHostRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!onRectChange) return;
    const node = rectHostRef.current;
    if (!node) return;
    if (typeof window === "undefined" || typeof ResizeObserver === "undefined") {
      return;
    }

    let frameId: number | null = null;
    const push = () => {
      frameId = null;
      const current = rectHostRef.current;
      if (!current) return;
      const r = current.getBoundingClientRect();
      onRectChange({ x: r.left, y: r.top, width: r.width, height: r.height });
    };
    const schedule = () => {
      if (frameId !== null) return;
      frameId = window.requestAnimationFrame(push);
    };

    const observer = new ResizeObserver(schedule);
    observer.observe(node);
    window.addEventListener("resize", schedule);
    schedule();

    return () => {
      observer.disconnect();
      window.removeEventListener("resize", schedule);
      if (frameId !== null) window.cancelAnimationFrame(frameId);
    };
  }, [onRectChange]);

  const kind = state?.kind ?? "idle";
  const running = kind === "running";
  const startable = kind === "idle" || kind === "exited";

  const wsUrl = state && state.kind === "running" ? state.ws_url : null;
  const sdkVersion = state && state.kind === "running" ? state.sdk_version : null;

  const tone =
    kind === "running"
      ? "success"
      : kind === "starting"
        ? "warning"
        : kind === "exited"
          ? "danger"
          : "muted";

  const onOpenSystemBrowser = useCallback(() => {
    if (!url) return;
    void import("@tauri-apps/api/core")
      .then(({ invoke }) => invoke("reveal_in_finder", { target: url }))
      .catch(() => {
        // F5.2 escape; best-effort. The Rust command lands with the
        // bundle / system-shell wiring, not in this UI commit.
      });
  }, [url]);

  return (
    <div className="flex h-full flex-col bg-background text-foreground">
      <header className="flex items-center gap-2 border-b border-border px-3 py-2">
        <div className="flex items-center gap-1">
          <Button
            variant="ghost"
            size="icon"
            disabled={!running}
            aria-label="Back"
            title="Back"
          >
            <ArrowLeft className="h-4 w-4" aria-hidden="true" />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            disabled={!running}
            aria-label="Forward"
            title="Forward"
          >
            <ArrowRight className="h-4 w-4" aria-hidden="true" />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            disabled={!running}
            aria-label="Reload"
            title="Reload"
          >
            <RefreshCw className="h-4 w-4" aria-hidden="true" />
          </Button>
        </div>
        <form
          className="flex flex-1 items-center"
          onSubmit={(event) => {
            event.preventDefault();
            onSubmit();
          }}
        >
          <label className="sr-only" htmlFor="browser-url">
            Address
          </label>
          <Input
            id="browser-url"
            type="url"
            value={url}
            onChange={(event) => onUrlChange(event.target.value)}
            placeholder="about:blank"
            disabled={!running}
            className="h-8"
            inputMode="url"
            spellCheck={false}
          />
        </form>
        <Button
          variant="ghost"
          size="icon"
          aria-label="Open in system browser"
          title="Open in system browser"
          disabled={!url}
          onClick={onOpenSystemBrowser}
        >
          <ExternalLink className="h-4 w-4" aria-hidden="true" />
        </Button>
      </header>
      <div className="flex items-center justify-between border-b border-border bg-muted/40 px-3 py-1.5 text-xs">
        <div className="flex items-center gap-2">
          <Badge tone={tone}>{browserStateLabel(state ?? { kind: "idle" })}</Badge>
          {sdkVersion ? (
            <span className="text-muted-foreground">
              CEF {sdkVersion}
            </span>
          ) : null}
          {wsUrl ? (
            <span
              className="font-mono text-muted-foreground"
              title={wsUrl}
            >
              {wsUrl.slice(0, 32)}
              {wsUrl.length > 32 ? "…" : ""}
            </span>
          ) : null}
        </div>
        <div className="flex items-center gap-2">
          {startable ? (
            <Button size="sm" disabled={busy} onClick={onStart}>
              Start browser
            </Button>
          ) : null}
          {running ? (
            <Button size="sm" variant="outline" disabled={busy} onClick={onStop}>
              Stop
            </Button>
          ) : null}
        </div>
      </div>
      {error ? (
        <div
          role="alert"
          className="border-b border-destructive/60 bg-destructive/10 px-3 py-1.5 text-xs text-foreground"
        >
          {error}
        </div>
      ) : null}
      <div
        ref={rectHostRef}
        data-thalyn-cef-host-rect
        className={cn(
          "relative flex-1 overflow-hidden",
          running ? "bg-transparent" : "bg-muted/20",
        )}
      >
        {!running ? (
          <div className="flex h-full flex-col items-center justify-center gap-2 text-muted-foreground">
            <Globe className="h-8 w-8" aria-hidden="true" />
            <p className="text-sm">
              {kind === "starting"
                ? "Starting bundled Chromium…"
                : kind === "exited"
                  ? `Engine exited${
                      state && state.kind === "exited" ? `: ${state.reason}` : ""
                    }`
                  : "Press Start browser to launch the bundled Chromium engine."}
            </p>
          </div>
        ) : null}
      </div>
    </div>
  );
}
