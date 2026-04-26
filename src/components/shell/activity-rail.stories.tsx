import type { Meta, StoryObj } from "@storybook/react-vite";
import { useState } from "react";

import { ThemeProvider } from "@/components/theme-provider";
import { ActivityRail } from "@/components/shell/activity-rail";

const meta = {
  title: "Shell/ActivityRail",
  component: ActivityRail,
  parameters: { layout: "fullscreen" },
  decorators: [
    (Story) => (
      <ThemeProvider>
        <div className="flex h-[420px] bg-background">
          <Story />
        </div>
      </ThemeProvider>
    ),
  ],
} satisfies Meta<typeof ActivityRail>;

export default meta;
type Story = StoryObj<typeof meta>;

export const ChatActive: Story = {
  args: { active: "chat" },
};

export const InteractiveCycle: Story = {
  render: () => {
    const [active, setActive] = useState("chat");
    return <ActivityRail active={active} onSelect={setActive} />;
  },
};
