import type { Meta, StoryObj } from "@storybook/react-vite";

import { InlineApprovalCard } from "@/components/approval/inline-approval-card";
import { ThemeProvider } from "@/components/theme-provider";
import type { Plan } from "@/lib/runs";

const SHORT_PLAN: Plan = {
  goal: "Fix the off-by-one error in the rate-limit window calculator.",
  nodes: [
    {
      id: "step_1",
      order: 0,
      description: "Add a regression test that pins the off-by-one.",
      rationale: "Catches the bug as a fixture before the change.",
      estimatedCost: { tokens: 1000 },
      status: "pending",
      parentId: null,
    },
    {
      id: "step_2",
      order: 1,
      description: "Patch the window-end inclusive check.",
      rationale: "Single-line fix; the test pins the behaviour.",
      estimatedCost: { tokens: 500 },
      status: "pending",
      parentId: null,
    },
    {
      id: "step_3",
      order: 2,
      description: "Re-run the integration suite.",
      rationale: "Sanity check across adjacent code paths.",
      estimatedCost: { tokens: 800 },
      status: "pending",
      parentId: null,
    },
  ],
};

const LONG_PLAN: Plan = {
  goal: "Refactor the auth middleware to use the new session API across all 6 services.",
  nodes: Array.from({ length: 7 }, (_, i) => ({
    id: `step_${i + 1}`,
    order: i,
    description: `Migrate service ${i + 1} to the new helper.`,
    rationale: "",
    estimatedCost: {},
    status: "pending" as const,
    parentId: null,
  })),
};

function Frame({ children }: { children: React.ReactNode }) {
  return (
    <ThemeProvider>
      <div className="w-[640px] bg-background p-4">{children}</div>
    </ThemeProvider>
  );
}

const meta = {
  title: "Approval/InlineApprovalCard",
  component: InlineApprovalCard,
  parameters: { layout: "fullscreen" },
  decorators: [
    (Story) => (
      <Frame>
        <Story />
      </Frame>
    ),
  ],
  args: {
    runId: "r_demo",
    providerId: "anthropic",
    plan: SHORT_PLAN,
    onSettled: () => undefined,
    onOpenEditor: () => undefined,
  },
} satisfies Meta<typeof InlineApprovalCard>;

export default meta;
type Story = StoryObj<typeof meta>;

export const ShortPlan: Story = {};

export const LongPlanCollapsed: Story = {
  args: { plan: LONG_PLAN },
};
