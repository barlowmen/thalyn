import type { Meta, StoryObj } from "@storybook/react-vite";

import { LogsView } from "@/components/logs/logs-surface";
import type { RunHeader } from "@/lib/runs";

const meta: Meta<typeof LogsView> = {
  title: "Logs/Surface",
  component: LogsView,
  parameters: { layout: "fullscreen" },
  decorators: [
    (Story) => (
      <div className="h-[640px] w-[820px] bg-background">
        <Story />
      </div>
    ),
  ],
  args: {
    busy: false,
    error: null,
    filter: [],
    onFilterChange: () => undefined,
    onRefresh: () => undefined,
    onOpen: () => undefined,
  },
};

export default meta;
type Story = StoryObj<typeof LogsView>;

const now = Date.UTC(2026, 3, 27, 14, 0, 0);

const run = (overrides: Partial<RunHeader>): RunHeader => ({
  runId: "run_log_00000000",
  projectId: null,
  parentRunId: null,
  status: "completed",
  title: "Untitled run",
  providerId: "anthropic",
  startedAtMs: now - 1000 * 60 * 30,
  completedAtMs: now - 1000 * 60 * 28,
  driftScore: 0,
  finalResponse: "",
  plan: null,
  sandboxTier: null,
  budget: null,
  budgetConsumed: null,
  ...overrides,
});

export const Loading: Story = {
  args: {
    runs: [],
    loading: true,
  },
};

export const Empty: Story = {
  args: {
    runs: [],
    loading: false,
  },
};

export const FilteredEmpty: Story = {
  args: {
    runs: [],
    loading: false,
    filter: ["errored"],
  },
};

export const Populated: Story = {
  args: {
    loading: false,
    runs: [
      run({
        runId: "run_top_001",
        title: "Plan email adapter test refactor",
        status: "running",
        startedAtMs: now - 1000 * 60 * 2,
        completedAtMs: null,
      }),
      run({
        runId: "run_sub_001",
        parentRunId: "run_top_001",
        title: "Write Gmail mock fixture",
        status: "completed",
        startedAtMs: now - 1000 * 60 * 25,
        completedAtMs: now - 1000 * 60 * 23,
      }),
      run({
        runId: "run_top_002",
        title: "Audit going-public checklist",
        status: "completed",
        startedAtMs: now - 1000 * 60 * 60 * 6,
        completedAtMs: now - 1000 * 60 * 60 * 5,
        driftScore: 0.18,
      }),
      run({
        runId: "run_top_003",
        title: "Sandbox escalation policy",
        status: "errored",
        startedAtMs: now - 1000 * 60 * 60 * 24,
        completedAtMs: now - 1000 * 60 * 60 * 23,
        driftScore: 0.71,
      }),
    ],
  },
};

export const ErrorState: Story = {
  args: {
    runs: [],
    loading: false,
    error: "Failed to load runs: brain sidecar not responding.",
  },
};
