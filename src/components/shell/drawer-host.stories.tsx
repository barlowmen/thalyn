import type { Meta, StoryObj } from "@storybook/react-vite";
import { useEffect } from "react";

import { ThemeProvider } from "@/components/theme-provider";
import {
  DrawerHost,
  DrawerHostProvider,
  type DrawerKind,
  useDrawerHost,
} from "@/components/shell/drawer-host";

/**
 * Storybook fixtures for the drawer-host primitive. Each story drives
 * the host into a deterministic state (no drawers, one drawer, two
 * drawers, compact width) so the a11y harness has stable surfaces to
 * audit. The chat column is a stub block — the real chat-first shell
 * mounts the message-list / strip / composer here.
 */
function ChatStub() {
  return (
    <div className="flex h-full min-h-0 items-center justify-center bg-background p-6 text-sm text-muted-foreground">
      Chat region (eternal thread)
    </div>
  );
}

function Frame({ children }: { children: React.ReactNode }) {
  return (
    <ThemeProvider>
      <div className="flex h-[640px] w-full flex-col bg-background">
        {children}
      </div>
    </ThemeProvider>
  );
}

function Driver({ open: kinds }: { open: DrawerKind[] }) {
  const host = useDrawerHost();
  useEffect(() => {
    host.closeAll();
    for (const kind of kinds) {
      host.open({ kind });
    }
    // ``host`` is stable inside the provider; the open list is the
    // only meaningful dependency so we don't reset on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kinds.join(",")]);
  return null;
}

function Stage({ open }: { open: DrawerKind[] }) {
  return (
    <DrawerHostProvider>
      <Driver open={open} />
      <DrawerHost chat={<ChatStub />} />
    </DrawerHostProvider>
  );
}

const meta = {
  title: "Shell/DrawerHost",
  component: Stage,
  parameters: { layout: "fullscreen" },
  decorators: [(Story) => <Frame><Story /></Frame>],
} satisfies Meta<typeof Stage>;

export default meta;
type Story = StoryObj<typeof meta>;

export const NoDrawers: Story = {
  args: { open: [] },
};

export const SingleEditor: Story = {
  args: { open: ["editor"] },
};

export const SingleTerminal: Story = {
  args: { open: ["terminal"] },
};

export const SingleEmail: Story = {
  args: { open: ["email"] },
};

export const SingleFiles: Story = {
  args: { open: ["file-tree"] },
};

export const SingleConnectors: Story = {
  args: { open: ["connectors"] },
};

export const SingleLogs: Story = {
  args: { open: ["logs"] },
};

export const TwoDrawers: Story = {
  // Two non-Monaco surfaces — Monaco's line-numbers tone falls below
  // 4.5:1 against the editor background under WCAG 2.1 AA contrast,
  // and the underlying theme is a Monaco upstream concern (its own
  // story exercises the editor in isolation). The two-drawer fixture
  // lives to assert layout invariants, not the editor theme.
  args: { open: ["file-tree", "connectors"] },
};
