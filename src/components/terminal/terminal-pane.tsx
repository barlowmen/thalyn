import { useEffect, useRef, useState } from "react";

import { FitAddon } from "@xterm/addon-fit";
import { Terminal } from "@xterm/xterm";

import {
  closeTerminal,
  openTerminal,
  resizeTerminal,
  subscribeTerminal,
  writeTerminal,
} from "@/lib/terminal";

import "@xterm/xterm/css/xterm.css";

/**
 * One xterm.js instance backed by a portable-pty session in the
 * Tauri host. Mounts the terminal, opens a pty, streams output
 * forward, sends input back, and resizes when the container does.
 */
export function TerminalPane({
  cwd,
  className,
}: {
  cwd?: string;
  className?: string;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;
    const container = containerRef.current;

    const term = new Terminal({
      fontFamily:
        "'Geist Mono', 'JetBrains Mono', ui-monospace, 'SF Mono', Menlo, monospace",
      fontSize: 13,
      lineHeight: 1.3,
      cursorBlink: true,
      cursorStyle: "block",
      convertEol: true,
      scrollback: 5_000,
      theme: terminalThemeForDocument(),
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(container);
    fit.fit();

    let unlisten: (() => void) | null = null;
    let sessionId: string | null = null;
    let cancelled = false;

    const start = async () => {
      try {
        const result = await openTerminal({
          cwd,
          cols: term.cols,
          rows: term.rows,
        });
        if (cancelled) {
          await closeTerminal(result.sessionId).catch(() => undefined);
          return;
        }
        sessionId = result.sessionId;
        if (result.snapshot) term.write(result.snapshot);
        unlisten = await subscribeTerminal(sessionId, (event) => {
          term.write(event.data);
        });
        term.onData((data) => {
          if (sessionId) void writeTerminal(sessionId, data);
        });
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      }
    };

    void start();

    const ro = new ResizeObserver(() => {
      try {
        fit.fit();
      } catch {
        // Container has zero size — ignore until it lays out.
      }
      if (sessionId) {
        void resizeTerminal(sessionId, term.cols, term.rows);
      }
    });
    ro.observe(container);

    return () => {
      cancelled = true;
      ro.disconnect();
      if (unlisten) unlisten();
      if (sessionId) void closeTerminal(sessionId).catch(() => undefined);
      term.dispose();
    };
  }, [cwd]);

  return (
    <div className={`flex h-full flex-col ${className ?? ""}`}>
      {error && (
        <p className="border-b border-border bg-destructive/10 px-3 py-1 text-[11px] text-danger">
          {error}
        </p>
      )}
      <div
        ref={containerRef}
        className="flex-1 overflow-hidden bg-background px-2 pt-1"
      />
    </div>
  );
}

function terminalThemeForDocument(): Record<string, string> {
  if (typeof document === "undefined") return DARK_TERMINAL_THEME;
  const attr = document.documentElement.getAttribute("data-theme");
  if (attr === "light") return LIGHT_TERMINAL_THEME;
  if (attr === "dark") return DARK_TERMINAL_THEME;
  if (typeof window !== "undefined" && window.matchMedia) {
    return window.matchMedia("(prefers-color-scheme: light)").matches
      ? LIGHT_TERMINAL_THEME
      : DARK_TERMINAL_THEME;
  }
  return DARK_TERMINAL_THEME;
}

const DARK_TERMINAL_THEME: Record<string, string> = {
  background: "#1d1d1d",
  foreground: "#f2f2f2",
  cursor: "#7ba6ff",
  cursorAccent: "#1d1d1d",
  selectionBackground: "#34487a",
  black: "#1d1d1d",
  red: "#e57373",
  green: "#81c784",
  yellow: "#ffd54f",
  blue: "#7ba6ff",
  magenta: "#ce93d8",
  cyan: "#80deea",
  white: "#f2f2f2",
  brightBlack: "#5e5e5e",
  brightRed: "#ef9a9a",
  brightGreen: "#a5d6a7",
  brightYellow: "#ffe082",
  brightBlue: "#9bb6ff",
  brightMagenta: "#ce93d8",
  brightCyan: "#80deea",
  brightWhite: "#fafafa",
};

const LIGHT_TERMINAL_THEME: Record<string, string> = {
  background: "#fdfdfd",
  foreground: "#262626",
  cursor: "#2c4caa",
  cursorAccent: "#fdfdfd",
  selectionBackground: "#c8d6f5",
  black: "#262626",
  red: "#c62828",
  green: "#2e7d32",
  yellow: "#9a7d00",
  blue: "#2c4caa",
  magenta: "#7b1fa2",
  cyan: "#00838f",
  white: "#f3f3f3",
  brightBlack: "#5e5e5e",
  brightRed: "#d84343",
  brightGreen: "#43a047",
  brightYellow: "#c69b00",
  brightBlue: "#3f5fc6",
  brightMagenta: "#9c27b0",
  brightCyan: "#0097a7",
  brightWhite: "#fafafa",
};
