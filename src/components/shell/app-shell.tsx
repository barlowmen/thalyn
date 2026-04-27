import { type ReactNode, useCallback, useRef, useState } from "react";
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

// Sanity bounds so a stuck-tiny layout from a prior buggy build
// can't trap the user at sub-min sizes after upgrade.
const SIDEBAR_BOUNDS = { min: 14, max: 30 } as const;
const INSPECTOR_BOUNDS = { min: 18, max: 36 } as const;

function clampLayout(layout: Layout): Layout {
  const sidebar = Math.min(
    SIDEBAR_BOUNDS.max,
    Math.max(SIDEBAR_BOUNDS.min, Number(layout.sidebar) || DEFAULT_LAYOUT.sidebar),
  );
  const inspector = Math.min(
    INSPECTOR_BOUNDS.max,
    Math.max(
      INSPECTOR_BOUNDS.min,
      Number(layout.inspector) || DEFAULT_LAYOUT.inspector,
    ),
  );
  const main = Math.max(30, 100 - sidebar - inspector);
  return { sidebar, main, inspector };
}

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
  const [schedulesOpen, setSchedulesOpen] = useState(false);
  const [memoryOpen, setMemoryOpen] = useState(false);

  const sidebarRef = useRef<PanelImperativeHandle | null>(null);
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
      // Connectors live inside the settings dialog (the marketplace +
      // grants UI). The rail icon is a shortcut into that section
      // rather than its own surface.
      if (id === "connectors") {
        openSettings();
        return;
      }
      setActiveRail(id);
    },
    [openSettings, openSchedules, openMemory],
  );

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-background text-foreground">
      <ActivityRail active={activeRail} onSelect={handleRailSelect} />

      <ResizableGroup
        id="thalyn-shell"
        orientation="horizontal"
        defaultLayout={defaultLayout}
        onLayoutChange={onLayoutChange}
        // min-w-0 lets the group shrink below its content's intrinsic
        // width inside the AppShell flex parent. Without this, the
        // panels' contents lock the group at content-size and resize
        // works in one direction only.
        className="min-w-0 flex-1"
      >
        <ResizablePanel
          id="sidebar"
          panelRef={sidebarRef}
          // Bare numbers are read as pixels by react-resizable-panels;
          // pass percentage strings ("14%", not 14) so the constraints
          // mean what we say they mean.
          defaultSize={`${defaultLayout.sidebar}%`}
          minSize="14%"
          maxSize="30%"
          collapsible
          collapsedSize="0%"
          // min-w-0 + overflow-hidden on the Panel's content wrapper
          // keeps the panel from being pushed wider than its flex
          // allocation by its own children.
          className="min-w-0 overflow-hidden"
        >
          <SidebarPanel />
        </ResizablePanel>

        <ResizableSeparator withHandle aria-label="Resize sidebar" />

        <ResizablePanel
          id="main"
          defaultSize={`${defaultLayout.main}%`}
          minSize="30%"
          className="min-w-0 overflow-hidden"
        >
          <section
            aria-label="Main"
            className="flex h-full flex-col bg-background"
          >
            <DriftGateLayer onReview={onOpenSubAgent} />
            {typeof main === "function"
              ? main({ openSettings, activeRail })
              : main}
          </section>
        </ResizablePanel>

        <ResizableSeparator withHandle aria-label="Resize inspector" />

        <ResizablePanel
          id="inspector"
          panelRef={inspectorRef}
          defaultSize={`${defaultLayout.inspector}%`}
          minSize="18%"
          maxSize="36%"
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
