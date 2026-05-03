import type { Meta, StoryObj } from "@storybook/react-vite";

import { ThemeProvider } from "@/components/theme-provider";
import { TopBar } from "@/components/shell/top-bar";

const meta = {
  title: "Shell/TopBar",
  component: TopBar,
  parameters: { layout: "fullscreen" },
  decorators: [
    (Story) => (
      <ThemeProvider>
        <div className="min-h-[120px] bg-background">
          <Story />
        </div>
      </ThemeProvider>
    ),
  ],
  args: {
    brainName: "Thalyn",
    activeProviderId: "anthropic",
    configured: true,
    activeProjectId: "proj_default",
    onOpenSettings: () => undefined,
  },
} satisfies Meta<typeof TopBar>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Configured: Story = {};

export const Unconfigured: Story = {
  args: {
    configured: false,
  },
};

export const ConfigPending: Story = {
  args: {
    configured: null,
  },
};
