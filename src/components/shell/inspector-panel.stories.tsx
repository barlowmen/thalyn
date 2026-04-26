import type { Meta, StoryObj } from "@storybook/react-vite";

import { InspectorPanel } from "@/components/shell/inspector-panel";

const meta: Meta<typeof InspectorPanel> = {
  title: "Shell/InspectorPanel",
  component: InspectorPanel,
  parameters: { layout: "fullscreen" },
  decorators: [
    (Story) => (
      <div className="h-[420px] w-[340px] bg-background">
        <Story />
      </div>
    ),
  ],
};

export default meta;
type Story = StoryObj<typeof InspectorPanel>;

export const Empty: Story = {};
