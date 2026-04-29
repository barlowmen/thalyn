import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { DrawerSurface } from "@/components/shell/drawer-surface";
import {
  resolveDrawer,
  resolveEscapeHatch,
} from "@/components/shell/drawer-content";
import { cn } from "@/lib/utils";

/**
 * The drawer kinds. Tools (editor / terminal / email / file-tree /
 * connectors / logs) plus the v0.28 agent surfaces (worker plan +
 * action-log detail). The browser drawer is reserved but doesn't
 * materialise until v0.29 (the cef-rs swap); the ``DrawerKind`` type
 * stays honest about the eventual surface set without forcing every
 * consumer to special-case its absence.
 */
export type DrawerKind =
  | "editor"
  | "terminal"
  | "email"
  | "file-tree"
  | "connectors"
  | "logs"
  | "worker";

/**
 * Per-kind open parameters. The brain (and Cmd-K) pass these through
 * to ``open()``; the drawer content component reads them on render.
 * Optional everywhere — opening an editor drawer with no path lands
 * on the scratch buffer, which is what the v1 surface already does.
 */
export type DrawerParams = {
  editor: { path?: string; line?: number };
  terminal: { cwd?: string };
  email: Record<string, never>;
  "file-tree": { root?: string };
  connectors: Record<string, never>;
  logs: { runId?: string };
  worker: { runId: string };
};

export type DrawerOpenSpec = {
  [K in DrawerKind]: { kind: K; params?: DrawerParams[K] };
}[DrawerKind];

type DrawerEntry<K extends DrawerKind = DrawerKind> = {
  kind: K;
  params: DrawerParams[K] | undefined;
};

type DrawerHostState = {
  /** Visible drawers, in open order (newest last). At most two. */
  visible: DrawerEntry[];
  /** Every kind that has ever been opened — these stay mounted with
   *  ``hidden`` set so internal tool state (Monaco buffers, xterm
   *  scroll, email selection) survives a dismiss/re-open round-trip. */
  mounted: DrawerEntry[];
};

type DrawerHostApi = DrawerHostState & {
  /** Open the named kind. If the kind is already open, it's brought to
   *  the focused position (rightmost slot). If two drawers are already
   *  open and the new kind isn't one of them, the oldest is closed to
   *  make room. */
  open<K extends DrawerKind>(spec: { kind: K; params?: DrawerParams[K] }): void;
  /** Close one kind — keeps the underlying mount alive so tool state
   *  survives the next ``open()``. */
  close(kind: DrawerKind): void;
  /** Close every visible drawer. ``mounted`` is unaffected. */
  closeAll(): void;
  isOpen(kind: DrawerKind): boolean;
};

const DrawerHostContext = createContext<DrawerHostApi | null>(null);

const DRAWER_BAND_STORAGE_KEY = "thalyn:drawer-band-width";
const DRAWER_BAND_MIN_PX = 320;
const DRAWER_BAND_DEFAULT_PX = 560;
const COMPACT_BREAKPOINT_PX = 900;
/** Fraction of the window width the chat region must keep when the
 *  drawer band is visible. Mirrors F8.2's "chat is always at least
 *  1/3 of the window" invariant — enforced numerically here so a CSS
 *  regression can't quietly violate it. */
const CHAT_MIN_FRACTION = 1 / 3;

export const DRAWER_TOOLS_OPEN_EVENT = "thalyn:tools-open";

export type DrawerToolsOpenDetail = DrawerOpenSpec;

/**
 * Dispatch a ``tools.open`` event the drawer host listens for. The
 * brain calls the matching Tauri event (``tools:open``); this helper
 * exists for in-app callers (Storybook controls, palette items, the
 * brain bridge) to share a single open path.
 */
export function dispatchToolsOpen(spec: DrawerOpenSpec): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(
    new CustomEvent<DrawerToolsOpenDetail>(DRAWER_TOOLS_OPEN_EVENT, {
      detail: spec,
    }),
  );
}

/**
 * The drawer-host provider. Owns the open-set / mounted-set state and
 * the brain-opened event listeners (window event + the Tauri
 * ``tools:open`` channel). Renders ``children`` so the consuming shell
 * can read the drawer state via ``useDrawerHost``.
 */
export function DrawerHostProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<DrawerHostState>({
    visible: [],
    mounted: [],
  });

  const open: DrawerHostApi["open"] = useCallback((spec) => {
    setState((current) => {
      const filtered = current.visible.filter((d) => d.kind !== spec.kind);
      const next: DrawerEntry[] = [
        ...filtered,
        { kind: spec.kind, params: spec.params },
      ];
      // Two-drawer cap (F8.2). Newest wins; oldest is dismissed.
      const trimmed = next.length > 2 ? next.slice(next.length - 2) : next;

      const mountedFiltered = current.mounted.filter(
        (d) => d.kind !== spec.kind,
      );
      const mountedNext: DrawerEntry[] = [
        ...mountedFiltered,
        { kind: spec.kind, params: spec.params },
      ];
      return { visible: trimmed, mounted: mountedNext };
    });
  }, []);

  const close: DrawerHostApi["close"] = useCallback((kind) => {
    setState((current) => ({
      ...current,
      visible: current.visible.filter((d) => d.kind !== kind),
    }));
  }, []);

  const closeAll: DrawerHostApi["closeAll"] = useCallback(() => {
    setState((current) => ({ ...current, visible: [] }));
  }, []);

  const isOpen: DrawerHostApi["isOpen"] = useCallback(
    (kind) => state.visible.some((d) => d.kind === kind),
    [state.visible],
  );

  // Window event channel — the Cmd-K palette and Storybook controls
  // dispatch through this so every caller shares a single open path.
  // The brain emits the same payload via the Tauri ``tools:open``
  // channel; listening to both avoids a divergence between the
  // human-driven and brain-driven open paths.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const onOpen = (event: Event) => {
      const detail = (event as CustomEvent<DrawerToolsOpenDetail>).detail;
      if (!detail || !detail.kind) return;
      open(detail);
    };
    window.addEventListener(DRAWER_TOOLS_OPEN_EVENT, onOpen);
    return () => window.removeEventListener(DRAWER_TOOLS_OPEN_EVENT, onOpen);
  }, [open]);

  // Tauri channel — the brain calls ``emit("tools:open", {kind, params})``
  // and the renderer routes it into the same store. ``listen`` is loaded
  // from the Tauri SDK; we tolerate its absence (storybook / playwright)
  // by silently ignoring the rejection — the window-event path keeps
  // working without it.
  useEffect(() => {
    let active = true;
    let unlisten: (() => void) | undefined;
    void import("@tauri-apps/api/event")
      .then(({ listen }) =>
        listen<DrawerToolsOpenDetail>("tools:open", (event) => {
          if (!active) return;
          if (!event.payload || !event.payload.kind) return;
          open(event.payload);
        }),
      )
      .then((fn) => {
        if (!active) {
          fn();
          return;
        }
        unlisten = fn;
      })
      .catch(() => {
        // No Tauri bridge — fall back to the window-event path.
      });
    return () => {
      active = false;
      unlisten?.();
    };
  }, [open]);

  // ⌘\ (or Ctrl-\) dismisses the focused (rightmost) drawer. The
  // shortcut is global so the user can dismiss without having to focus
  // the drawer first; capturing on ``window`` matches how Cmd-K is
  // wired in the command palette.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const onKey = (event: KeyboardEvent) => {
      const isToggle =
        event.key === "\\" && (event.metaKey || event.ctrlKey);
      if (!isToggle) return;
      // The drawer host owns ⌘\\; preventDefault stops the browser
      // from inserting a literal backslash if the focus is on the
      // composer or any other text input.
      event.preventDefault();
      setState((current) => {
        if (current.visible.length === 0) return current;
        return {
          ...current,
          visible: current.visible.slice(0, -1),
        };
      });
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const api = useMemo<DrawerHostApi>(
    () => ({ ...state, open, close, closeAll, isOpen }),
    [state, open, close, closeAll, isOpen],
  );

  return (
    <DrawerHostContext.Provider value={api}>
      {children}
    </DrawerHostContext.Provider>
  );
}

/**
 * Read the drawer-host API from context. Consumers anywhere below
 * ``DrawerHostProvider`` (palette, top bar, chat, etc.) call this to
 * open or dismiss drawers.
 */
export function useDrawerHost(): DrawerHostApi {
  const ctx = useContext(DrawerHostContext);
  if (!ctx) {
    throw new Error(
      "useDrawerHost must be used inside <DrawerHostProvider>",
    );
  }
  return ctx;
}

/**
 * Layout component. Renders the chat column (passed as ``chat``) on
 * the left and the drawer band on the right, with a draggable handle
 * for resize between them. Below ``COMPACT_BREAKPOINT_PX`` the band
 * takes the chat column's place (chat hides) — F8.2 calls this out
 * for narrow windows.
 *
 * Every kind that has ever been opened stays mounted; visibility is
 * driven by ``hidden``. That's the cheapest way to preserve tool
 * state across a dismiss/re-open round-trip without per-surface state
 * lifting.
 */
export function DrawerHost({
  chat,
  className,
}: {
  /** The chat column content. Wrapped in a flex container so its
   *  inner layout (message list / strip / composer) stays unchanged. */
  chat: ReactNode;
  className?: string;
}) {
  const { visible: openDrawers, mounted, close } = useDrawerHost();
  const [bandWidth, setBandWidth] = useState<number>(() => loadBandWidth());
  const [windowWidth, setWindowWidth] = useState<number>(() =>
    typeof window === "undefined" ? 1440 : window.innerWidth,
  );
  const dragRef = useRef<{ startX: number; startWidth: number } | null>(null);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const onResize = () => setWindowWidth(window.innerWidth);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  const drawerCount = openDrawers.length;
  const isCompact = windowWidth < COMPACT_BREAKPOINT_PX;
  const hasDrawers = drawerCount > 0;

  // Clamp the band width to the chat-≥-1/3 invariant. The clamp runs
  // every render (cheap) so the invariant holds even if the user
  // resizes the window mid-drag.
  const maxBandWidth = Math.max(
    DRAWER_BAND_MIN_PX,
    Math.floor(windowWidth * (1 - CHAT_MIN_FRACTION)),
  );
  const effectiveBandWidth = Math.min(
    Math.max(bandWidth, DRAWER_BAND_MIN_PX),
    maxBandWidth,
  );

  // Compact mode: drawer band fills the window when any drawer is
  // open, hiding chat entirely. Width math collapses to "100% / 0%".
  const chatHidden = isCompact && hasDrawers;

  const onDragStart = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      event.preventDefault();
      dragRef.current = {
        startX: event.clientX,
        startWidth: effectiveBandWidth,
      };
      const target = event.currentTarget;
      target.setPointerCapture(event.pointerId);
    },
    [effectiveBandWidth],
  );

  const onDragMove = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      const drag = dragRef.current;
      if (!drag) return;
      // Drag handle sits on the left edge of the band; dragging right
      // shrinks the band, dragging left grows it.
      const delta = drag.startX - event.clientX;
      const next = Math.min(
        Math.max(drag.startWidth + delta, DRAWER_BAND_MIN_PX),
        maxBandWidth,
      );
      setBandWidth(next);
    },
    [maxBandWidth],
  );

  const onDragEnd = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      const drag = dragRef.current;
      if (!drag) return;
      dragRef.current = null;
      const target = event.currentTarget;
      if (target.hasPointerCapture(event.pointerId)) {
        target.releasePointerCapture(event.pointerId);
      }
      saveBandWidth(effectiveBandWidth);
    },
    [effectiveBandWidth],
  );

  // When two drawers are visible, they each take half of the band.
  // When one drawer is visible, it takes the whole band. This is the
  // intentional simplification for v0.27 — drawer-vs-drawer resize
  // lands later if the equal split turns out to be wrong.
  const visibleKinds = useMemo(
    () => new Set(openDrawers.map((d) => d.kind)),
    [openDrawers],
  );

  return (
    <div
      className={cn(
        "flex min-h-0 min-w-0 flex-1",
        className,
      )}
    >
      <div
        className={cn(
          "flex min-h-0 min-w-0 flex-col overflow-hidden",
          "transition-[flex-basis] duration-200 ease-out motion-reduce:transition-none",
          chatHidden ? "hidden" : "flex-1",
        )}
      >
        {chat}
      </div>

      {hasDrawers && (
        <>
          <div
            role="separator"
            aria-orientation="vertical"
            aria-label="Resize drawer"
            // ARIA window-splitter pattern — focusable separator
            // requires the current / min / max values so screen
            // readers announce the band's size as a percentage.
            aria-valuenow={Math.round(
              ((effectiveBandWidth - DRAWER_BAND_MIN_PX) /
                Math.max(1, maxBandWidth - DRAWER_BAND_MIN_PX)) *
                100,
            )}
            aria-valuemin={0}
            aria-valuemax={100}
            tabIndex={0}
            onPointerDown={onDragStart}
            onPointerMove={onDragMove}
            onPointerUp={onDragEnd}
            onPointerCancel={onDragEnd}
            onKeyDown={(event) => {
              // Keyboard parity (F8.12) — arrow keys nudge the band
              // 24 px at a time, Home / End jump to bounds.
              const STEP = 24;
              if (event.key === "ArrowLeft") {
                event.preventDefault();
                setBandWidth((w) =>
                  Math.min(maxBandWidth, Math.max(DRAWER_BAND_MIN_PX, w + STEP)),
                );
              } else if (event.key === "ArrowRight") {
                event.preventDefault();
                setBandWidth((w) =>
                  Math.min(maxBandWidth, Math.max(DRAWER_BAND_MIN_PX, w - STEP)),
                );
              } else if (event.key === "Home") {
                event.preventDefault();
                setBandWidth(maxBandWidth);
              } else if (event.key === "End") {
                event.preventDefault();
                setBandWidth(DRAWER_BAND_MIN_PX);
              } else {
                return;
              }
              saveBandWidth(bandWidth);
            }}
            className={cn(
              "group relative flex h-full w-1 shrink-0 cursor-col-resize items-center justify-center bg-border",
              "hover:bg-primary/40 focus-visible:bg-primary/60 focus-visible:outline-none",
              isCompact && "hidden",
            )}
          >
            <span
              aria-hidden
              className="pointer-events-none absolute inset-y-0 -left-1 w-3"
            />
          </div>

          <div
            aria-label="Drawers"
            className={cn(
              "flex min-h-0 shrink-0 overflow-hidden bg-background",
              "transition-[width] duration-200 ease-out motion-reduce:transition-none",
            )}
            style={{
              width: chatHidden ? "100%" : `${effectiveBandWidth}px`,
            }}
          >
            {mounted.map((entry) => {
              const isVisible = visibleKinds.has(entry.kind);
              const visibleIndex = openDrawers.findIndex(
                (d) => d.kind === entry.kind,
              );
              const flexBasis =
                drawerCount === 2 ? "50%" : drawerCount === 1 ? "100%" : "0%";
              return (
                <div
                  key={entry.kind}
                  data-kind={entry.kind}
                  // ``hidden`` keeps the React tree (and the underlying
                  // tool state) intact while removing the surface from
                  // the layout. ``order`` follows ``visibleIndex`` so
                  // re-opening the same kind brings it back to its slot
                  // in the band rather than shuffling its neighbour.
                  hidden={!isVisible}
                  className="flex min-h-0 min-w-0 flex-col border-l border-border first:border-l-0"
                  style={{
                    flexBasis: isVisible ? flexBasis : "0%",
                    flexGrow: isVisible ? 1 : 0,
                    flexShrink: isVisible ? 1 : 0,
                    order: isVisible ? visibleIndex : 99,
                  }}
                >
                  <DrawerSurface
                    kind={entry.kind}
                    onClose={() => close(entry.kind)}
                    escapeHatch={resolveEscapeHatch(entry.kind, entry.params)}
                  >
                    {resolveDrawer(entry.kind, entry.params)}
                  </DrawerSurface>
                </div>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}

function loadBandWidth(): number {
  if (typeof window === "undefined") return DRAWER_BAND_DEFAULT_PX;
  try {
    const raw = window.localStorage.getItem(DRAWER_BAND_STORAGE_KEY);
    if (!raw) return DRAWER_BAND_DEFAULT_PX;
    const parsed = Number(raw);
    if (!Number.isFinite(parsed)) return DRAWER_BAND_DEFAULT_PX;
    return Math.max(parsed, DRAWER_BAND_MIN_PX);
  } catch {
    return DRAWER_BAND_DEFAULT_PX;
  }
}

function saveBandWidth(width: number): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(DRAWER_BAND_STORAGE_KEY, String(width));
  } catch {
    // best-effort
  }
}
