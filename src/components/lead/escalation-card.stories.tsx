import type { Meta, StoryObj } from "@storybook/react-vite";

import { EscalationCard } from "@/components/lead/escalation-card";
import { ThemeProvider } from "@/components/theme-provider";

function Frame({ children }: { children: React.ReactNode }) {
  return (
    <ThemeProvider>
      <div className="w-[640px] bg-background p-4">{children}</div>
    </ThemeProvider>
  );
}

const meta = {
  title: "Lead/EscalationCard",
  component: EscalationCard,
  parameters: { layout: "fullscreen" },
  decorators: [
    (Story) => (
      <Frame>
        <Story />
      </Frame>
    ),
  ],
  args: {
    signal: {
      leadId: "lead_thalyn",
      questionCount: 6,
      density: "high",
      suggestion: "open_drawer",
    },
    displayName: "Lead-Thalyn",
    onAccept: () => undefined,
    onDismiss: () => undefined,
  },
} satisfies Meta<typeof EscalationCard>;

export default meta;
type Story = StoryObj<typeof meta>;

export const HighDensity: Story = {};

export const SingleQuestion: Story = {
  args: {
    signal: {
      leadId: "lead_thalyn",
      questionCount: 1,
      density: "high",
      suggestion: "open_drawer",
    },
  },
};
