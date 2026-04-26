import { type ReactNode, useCallback, useState } from "react";
import type { Layout } from "react-resizable-panels";

import { ActivityRail } from "@/components/shell/activity-rail";
import { InspectorPanel } from "@/components/shell/inspector-panel";
import { SidebarPanel } from "@/components/shell/sidebar-panel";
import {
  ResizableGroup,
  ResizablePanel,
  ResizableSeparator,
} from "@/components/ui/resizable";

/**
 * The three-panel mosaic shell:
 *
 *   ┌──────┬─────────────┬─────────────────────────┬──────────────┐
 *   │ rail │  sidebar    │  main                   │  inspector   │
 *   │ 56px │ 200–360 px  │  fluid                  │  280–480 px  │
 *   └──────┴─────────────┴─────────────────────────┴──────────────┘
 *
 * Sidebar and inspector are collapsible (drag the separator to the
 * edge). The layout is persisted to localStorage; a per-project key
 * lands when projects come online — for now there is one shared key.
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
    // Storage may be full or disabled; persistence is best-effort.
  }
}

export function AppShell({ main }: { main: ReactNode }) {
  const [activeRail, setActiveRail] = useState<string>("chat");
  const [defaultLayout] = useState<Layout>(() => loadLayout());

  const onLayoutChange = useCallback((layout: Layout) => {
    saveLayout(layout);
  }, []);

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-background text-foreground">
      <ActivityRail active={activeRail} onSelect={setActiveRail} />

      <ResizableGroup
        id="thalyn-shell"
        orientation="horizontal"
        defaultLayout={defaultLayout}
        onLayoutChange={onLayoutChange}
        className="flex-1"
      >
        <ResizablePanel
          id="sidebar"
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
            {main}
          </section>
        </ResizablePanel>

        <ResizableSeparator />

        <ResizablePanel
          id="inspector"
          defaultSize={defaultLayout.inspector}
          minSize={18}
          maxSize={36}
          collapsible
          collapsedSize={0}
        >
          <InspectorPanel />
        </ResizablePanel>
      </ResizableGroup>
    </div>
  );
}
