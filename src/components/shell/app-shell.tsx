import { type ReactNode, useCallback, useRef, useState } from "react";
import type {
  Layout,
  PanelImperativeHandle,
} from "react-resizable-panels";

import { PlanApprovalDialog } from "@/components/approval/plan-approval-dialog";
import { useApprovalGate } from "@/components/approval/use-approval-gate";
import { CommandPalette } from "@/components/command-palette";
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
};

/**
 * The three-panel mosaic shell:
 *
 *   ┌──────┬─────────────┬─────────────────────────┬──────────────┐
 *   │ rail │  sidebar    │  main                   │  inspector   │
 *   │ 56px │ 200–360 px  │  fluid                  │  280–480 px  │
 *   └──────┴─────────────┴─────────────────────────┴──────────────┘
 *
 * Sidebar and inspector are collapsible — by drag-to-edge or via the
 * command palette. Layout is persisted per shell instance to
 * localStorage; a per-project key lands when projects come online.
 */
const STORAGE_KEY = "thalyn:layout:default";
const DEFAULT_LAYOUT: Layout = {
  sidebar: 20,
  main: 55,
  inspector: 25,
};

function loadLayout(): Layout {
  if (typeof window === "undefined") return DEFAULT_LAYOUT;
  const raw = window.localStorage.getItem(STORAGE_KEY);
  if (!raw) return DEFAULT_LAYOUT;
  try {
    const parsed = JSON.parse(raw) as unknown;
    if (
      typeof parsed === "object" &&
      parsed !== null &&
      "sidebar" in parsed &&
      "main" in parsed &&
      "inspector" in parsed
    ) {
      return parsed as Layout;
    }
  } catch {
    // fall through to default
  }
  return DEFAULT_LAYOUT;
}

function saveLayout(layout: Layout): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(layout));
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
  main,
  openSubAgentRunId,
  onOpenSubAgent,
}: {
  main: ReactNode | ((api: ShellApi) => ReactNode);
  openSubAgentRunId?: string | null;
  onOpenSubAgent?: (runId: string) => void;
}) {
  const [activeRail, setActiveRail] = useState<string>("chat");
  const [defaultLayout] = useState<Layout>(() => loadLayout());
  const [settingsOpen, setSettingsOpen] = useState(false);

  const sidebarRef = useRef<PanelImperativeHandle | null>(null);
  const inspectorRef = useRef<PanelImperativeHandle | null>(null);

  const onLayoutChange = useCallback((layout: Layout) => {
    saveLayout(layout);
  }, []);

  const toggleSidebar = useCallback(() => togglePanel(sidebarRef), []);
  const toggleInspector = useCallback(() => togglePanel(inspectorRef), []);
  const openSettings = useCallback(() => setSettingsOpen(true), []);

  const handleRailSelect = useCallback(
    (id: string) => {
      if (id === "settings") {
        openSettings();
        return;
      }
      setActiveRail(id);
    },
    [openSettings],
  );

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-background text-foreground">
      <ActivityRail active={activeRail} onSelect={handleRailSelect} />

      <ResizableGroup
        id="thalyn-shell"
        orientation="horizontal"
        defaultLayout={defaultLayout}
        onLayoutChange={onLayoutChange}
        className="flex-1"
      >
        <ResizablePanel
          id="sidebar"
          panelRef={sidebarRef}
          defaultSize={defaultLayout.sidebar}
          minSize={14}
          maxSize={30}
          collapsible
          collapsedSize={0}
        >
          <SidebarPanel />
        </ResizablePanel>

        <ResizableSeparator />

        <ResizablePanel
          id="main"
          defaultSize={defaultLayout.main}
          minSize={30}
        >
          <section
            aria-label="Main"
            className="flex h-full flex-col bg-background"
          >
            {typeof main === "function" ? main({ openSettings }) : main}
          </section>
        </ResizablePanel>

        <ResizableSeparator />

        <ResizablePanel
          id="inspector"
          panelRef={inspectorRef}
          defaultSize={defaultLayout.inspector}
          minSize={18}
          maxSize={36}
          collapsible
          collapsedSize={0}
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
