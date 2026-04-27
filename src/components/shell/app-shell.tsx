import { type ReactNode, useCallback, useEffect, useRef, useState } from "react";
import type {
  Layout,
  PanelImperativeHandle,
} from "react-resizable-panels";

import { PlanApprovalDialog } from "@/components/approval/plan-approval-dialog";
import { useApprovalGate } from "@/components/approval/use-approval-gate";
import { CommandPalette } from "@/components/command-palette";
import { DriftGateBanner } from "@/components/inspector/drift-gate-banner";
import { useDriftGate } from "@/components/inspector/use-drift-gate";
import { MemoryDialog } from "@/components/memory/memory-dialog";
import { SchedulesDialog } from "@/components/schedules/schedules-dialog";
import { SettingsDialog } from "@/components/settings/settings-dialog";
import { ActivityRail } from "@/components/shell/activity-rail";
import { InspectorPanel } from "@/components/shell/inspector-panel";
import { SidebarPanel } from "@/components/shell/sidebar-panel";
import {
  ResizableGroup,
  ResizablePanel,
  ResizableSeparator,
} from "@/components/ui/resizable";
import { readActiveProvider } from "@/lib/active-provider";

export type ShellApi = {
  openSettings: () => void;
  activeRail: string;
  /**
   * Collapse the surface region back to chat-only mode. Surfaces
   * call this from a close button in their header so the user has
   * an explicit dismiss affordance and isn't stuck guessing that
   * the chat rail icon is the only way out.
   */
  closeSurface: () => void;
};

/**
 * The four-panel mosaic shell:
 *
 *   ┌──────┬──────────┬──────────┬──────────┬──────────┐
 *   │ rail │ sidebar  │ surface  │  chat    │inspector │
 *   │ 56px │ 14–30%   │ 0–60%    │ 22–60%   │ 18–36%   │
 *   └──────┴──────────┴──────────┴──────────┴──────────┘
 *
 * The brain chat is permanent (never collapsible) so the user
 * always has the brain reachable, no matter what surface is in
 * focus. The surface region holds whatever rail item is selected
 * (editor / terminal / browser / email / agents / logs /
 * connectors / sub-agent detail) and collapses to nothing when the
 * user clicks the chat rail icon — that's the dedicated
 * full-conversation mode.
 *
 * Sidebar and inspector are independently collapsible. Layout is
 * persisted per shell instance to localStorage.
 */
const STORAGE_KEY = "thalyn:layout:default";
const LAYOUT_VERSION = 2;
const DEFAULT_LAYOUT: Layout = {
  sidebar: 18,
  surface: 32,
  chat: 30,
  inspector: 20,
};

const SIDEBAR_BOUNDS = { min: 14, max: 30 } as const;
const INSPECTOR_BOUNDS = { min: 18, max: 36 } as const;
const SURFACE_BOUNDS = { min: 22, max: 60 } as const;
const CHAT_BOUNDS = { min: 22, max: 60 } as const;

type StoredLayout = Layout & { _v?: number };

function clampLayout(layout: Layout): Layout {
  const sidebar = clamp(
    Number(layout.sidebar) || DEFAULT_LAYOUT.sidebar,
    SIDEBAR_BOUNDS.min,
    SIDEBAR_BOUNDS.max,
  );
  const inspector = clamp(
    Number(layout.inspector) || DEFAULT_LAYOUT.inspector,
    INSPECTOR_BOUNDS.min,
    INSPECTOR_BOUNDS.max,
  );

  // Surface may be 0 (collapsed). Chat must be > 0 (the brain
  // is always reachable). When the inputs don't add up cleanly
  // (e.g. migrating from the prior 3-key layout), fall back to
  // the proportional defaults for the surface + chat split.
  const remainder = Math.max(0, 100 - sidebar - inspector);
  const requestedSurface = Number(layout.surface);
  const requestedChat = Number(layout.chat);

  let surface: number;
  let chat: number;
  if (Number.isFinite(requestedSurface) && Number.isFinite(requestedChat)) {
    surface = clamp(requestedSurface, 0, SURFACE_BOUNDS.max);
    chat = clamp(requestedChat, CHAT_BOUNDS.min, CHAT_BOUNDS.max);
    const total = surface + chat;
    if (total === 0) {
      chat = remainder;
      surface = 0;
    } else {
      // Scale so the pair fills the remainder exactly.
      const scale = remainder / total;
      surface = surface * scale;
      chat = chat * scale;
    }
  } else {
    // Migrating from a 3-key layout — keep chat at default share,
    // hand the rest to the surface.
    chat = clamp(
      (DEFAULT_LAYOUT.chat / (DEFAULT_LAYOUT.surface + DEFAULT_LAYOUT.chat)) *
        remainder,
      CHAT_BOUNDS.min,
      CHAT_BOUNDS.max,
    );
    surface = Math.max(0, remainder - chat);
  }

  return { sidebar, surface, chat, inspector };
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function loadLayout(): Layout {
  if (typeof window === "undefined") return DEFAULT_LAYOUT;
  const raw = window.localStorage.getItem(STORAGE_KEY);
  if (!raw) return DEFAULT_LAYOUT;
  try {
    const parsed = JSON.parse(raw) as Partial<StoredLayout> | null;
    if (
      parsed &&
      typeof parsed === "object" &&
      "sidebar" in parsed &&
      "inspector" in parsed
    ) {
      return clampLayout(parsed as Layout);
    }
  } catch {
    // fall through to default
  }
  return DEFAULT_LAYOUT;
}

function saveLayout(layout: Layout): void {
  if (typeof window === "undefined") return;
  try {
    const stored: StoredLayout = { ...layout, _v: LAYOUT_VERSION };
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(stored));
  } catch {
    // best-effort — storage may be full or disabled
  }
}

function togglePanel(ref: React.RefObject<PanelImperativeHandle | null>) {
  const handle = ref.current;
  if (!handle) return;
  if (handle.isCollapsed()) {
    handle.expand();
  } else {
    handle.collapse();
  }
}

export function AppShell({
  surface,
  chat,
  openSubAgentRunId,
  onOpenSubAgent,
  onCloseSurface,
}: {
  /**
   * Renders into the surface region (left of chat). Returning ``null``
   * is allowed — the shell will collapse the surface panel for you,
   * which is also what happens when the user clicks the chat rail
   * icon.
   */
  surface: ReactNode | ((api: ShellApi) => ReactNode);
  /**
   * Renders into the chat region (right of surface). Always shown —
   * this is the persistent brain panel. Receives the same shell API
   * so chat-side dialogs (settings, etc.) work the same.
   */
  chat: ReactNode | ((api: ShellApi) => ReactNode);
  openSubAgentRunId?: string | null;
  onOpenSubAgent?: (runId: string) => void;
  /**
   * Called when the user dismisses the surface from a surface-side
   * close button. The shell uses this to clear any open sub-agent
   * (which is the surface-displacing case the rail can't speak to).
   */
  onCloseSurface?: () => void;
}) {
  const [activeRail, setActiveRail] = useState<string>("chat");
  const [defaultLayout] = useState<Layout>(() => loadLayout());
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [schedulesOpen, setSchedulesOpen] = useState(false);
  const [memoryOpen, setMemoryOpen] = useState(false);

  const sidebarRef = useRef<PanelImperativeHandle | null>(null);
  const surfaceRef = useRef<PanelImperativeHandle | null>(null);
  const inspectorRef = useRef<PanelImperativeHandle | null>(null);

  const onLayoutChange = useCallback((layout: Layout) => {
    saveLayout(layout);
  }, []);

  const toggleSidebar = useCallback(() => togglePanel(sidebarRef), []);
  const toggleInspector = useCallback(() => togglePanel(inspectorRef), []);
  const openSettings = useCallback(() => setSettingsOpen(true), []);
  const openSchedules = useCallback(() => setSchedulesOpen(true), []);
  const openMemory = useCallback(() => setMemoryOpen(true), []);

  const handleRailSelect = useCallback(
    (id: string) => {
      if (id === "settings") {
        openSettings();
        return;
      }
      if (id === "schedules") {
        openSchedules();
        return;
      }
      if (id === "memory") {
        openMemory();
        return;
      }
      setActiveRail(id);
    },
    [openSettings, openSchedules, openMemory],
  );

  // Collapse the surface region when the user is in chat-only mode,
  // expand it whenever they pick a real surface OR open a sub-agent
  // (which renders its detail into the surface). Imperative because
  // react-resizable-panels owns the live size; we drive it via the
  // panel ref to keep persistence + collapse animation correct.
  useEffect(() => {
    const handle = surfaceRef.current;
    if (!handle) return;
    const wantCollapsed = activeRail === "chat" && !openSubAgentRunId;
    const isCollapsed = handle.isCollapsed();
    if (wantCollapsed && !isCollapsed) handle.collapse();
    if (!wantCollapsed && isCollapsed) handle.expand();
  }, [activeRail, openSubAgentRunId]);

  const closeSurface = useCallback(() => {
    setActiveRail("chat");
    onCloseSurface?.();
  }, [onCloseSurface]);

  const api: ShellApi = { openSettings, activeRail, closeSurface };
  const renderSurface = typeof surface === "function" ? surface(api) : surface;
  const renderChat = typeof chat === "function" ? chat(api) : chat;

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-background text-foreground">
      <ActivityRail active={activeRail} onSelect={handleRailSelect} />

      <ResizableGroup
        id="thalyn-shell"
        orientation="horizontal"
        defaultLayout={defaultLayout}
        onLayoutChange={onLayoutChange}
        // min-w-0 lets the group shrink below its content's intrinsic
        // width inside the AppShell flex parent.
        className="min-w-0 flex-1"
      >
        <ResizablePanel
          id="sidebar"
          panelRef={sidebarRef}
          defaultSize={`${defaultLayout.sidebar}%`}
          minSize={`${SIDEBAR_BOUNDS.min}%`}
          maxSize={`${SIDEBAR_BOUNDS.max}%`}
          collapsible
          collapsedSize="0%"
          className="min-w-0 overflow-hidden"
        >
          <SidebarPanel />
        </ResizablePanel>

        <ResizableSeparator withHandle aria-label="Resize sidebar" />

        <ResizablePanel
          id="surface"
          panelRef={surfaceRef}
          defaultSize={`${defaultLayout.surface}%`}
          // The chat-only mode collapses this panel to 0; otherwise
          // the surface holds whatever the active rail item renders.
          minSize={`${SURFACE_BOUNDS.min}%`}
          maxSize={`${SURFACE_BOUNDS.max}%`}
          collapsible
          collapsedSize="0%"
          className="min-w-0 overflow-hidden"
        >
          <section
            aria-label="Surface"
            className="flex h-full flex-col bg-background"
          >
            <DriftGateLayer onReview={onOpenSubAgent} />
            {renderSurface}
          </section>
        </ResizablePanel>

        <ResizableSeparator withHandle aria-label="Resize surface" />

        <ResizablePanel
          id="chat"
          // Chat is intentionally NOT collapsible. The brain is the
          // focal point of the app; it never goes away.
          defaultSize={`${defaultLayout.chat}%`}
          minSize={`${CHAT_BOUNDS.min}%`}
          maxSize={`${CHAT_BOUNDS.max}%`}
          className="min-w-0 overflow-hidden"
        >
          <section
            aria-label="Chat"
            className="flex h-full flex-col bg-background"
          >
            {renderChat}
          </section>
        </ResizablePanel>

        <ResizableSeparator withHandle aria-label="Resize inspector" />

        <ResizablePanel
          id="inspector"
          panelRef={inspectorRef}
          defaultSize={`${defaultLayout.inspector}%`}
          minSize={`${INSPECTOR_BOUNDS.min}%`}
          maxSize={`${INSPECTOR_BOUNDS.max}%`}
          collapsible
          collapsedSize="0%"
          className="min-w-0 overflow-hidden"
        >
          <InspectorPanel
            onOpenSubAgent={onOpenSubAgent}
            highlightedRunId={openSubAgentRunId ?? null}
          />
        </ResizablePanel>
      </ResizableGroup>

      <CommandPalette
        onToggleSidebar={toggleSidebar}
        onToggleInspector={toggleInspector}
        onOpenSettings={openSettings}
      />

      <SettingsDialog open={settingsOpen} onOpenChange={setSettingsOpen} />
      <SchedulesDialog
        open={schedulesOpen}
        onOpenChange={setSchedulesOpen}
        defaultProviderId={readActiveProvider()}
      />
      <MemoryDialog open={memoryOpen} onOpenChange={setMemoryOpen} />

      <ApprovalLayer />
    </div>
  );
}

function ApprovalLayer() {
  const { gate, clear } = useApprovalGate();
  return (
    <PlanApprovalDialog
      open={gate !== null}
      runId={gate?.runId ?? null}
      providerId={readActiveProvider()}
      plan={gate?.plan ?? null}
      onSettled={clear}
    />
  );
}

function DriftGateLayer({
  onReview,
}: {
  onReview?: (runId: string) => void;
}) {
  const { gate, dismiss } = useDriftGate();
  return <DriftGateBanner gate={gate} onReview={onReview} onDismiss={dismiss} />;
}
