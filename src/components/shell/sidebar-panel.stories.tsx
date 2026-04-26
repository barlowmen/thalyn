import type { Meta, StoryObj } from "@storybook/react-vite";

import { SidebarPanel } from "@/components/shell/sidebar-panel";

const meta = {
  title: "Shell/SidebarPanel",
  component: SidebarPanel,
  parameters: { layout: "fullscreen" },
  decorators: [
    (Story) => (
      <div className="h-[420px] w-[280px] bg-background">
        <Story />
      </div>
    ),
  ],
} satisfies Meta<typeof SidebarPanel>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Empty: Story = {};
