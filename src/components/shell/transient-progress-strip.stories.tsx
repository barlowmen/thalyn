import type { Meta, StoryObj } from "@storybook/react-vite";

import { ThemeProvider } from "@/components/theme-provider";
import { TransientProgressStrip } from "@/components/shell/transient-progress-strip";

const meta = {
  title: "Shell/TransientProgressStrip",
  component: TransientProgressStrip,
  parameters: { layout: "fullscreen" },
  decorators: [
    (Story) => (
      <ThemeProvider>
        <div className="min-h-[80px] bg-background">
          <Story />
        </div>
      </ThemeProvider>
    ),
  ],
} satisfies Meta<typeof TransientProgressStrip>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Idle: Story = {
  args: { activity: null },
};

export const Sending: Story = {
  args: {
    activity: { kind: "sending", label: "Routing your turn through Thalyn…" },
  },
};

export const RunningClickable: Story = {
  args: {
    activity: {
      kind: "running",
      label: "Sam is working — refactor auth handler",
      onClick: () => undefined,
    },
  },
};

export const AwaitingApproval: Story = {
  args: {
    activity: {
      kind: "awaiting_approval",
      label: "Plan ready for review — open to approve or edit.",
      onClick: () => undefined,
    },
  },
};

export const DriftFlag: Story = {
  args: {
    activity: {
      kind: "drift",
      label: "Drift flagged on the docs-rewrite run.",
      onClick: () => undefined,
    },
  },
};
