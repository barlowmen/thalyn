import type { Meta, StoryObj } from "@storybook/react-vite";

import { ThemeProvider } from "@/components/theme-provider";
import {
  type RunDetail,
  detailFromHeader,
} from "@/components/worker/use-run-detail";
import { WorkerSurface } from "@/components/worker/worker-surface";
import type { ActionLogEntry, Plan, RunHeader } from "@/lib/runs";

/**
 * Storybook fixtures for the worker drawer surface. Each story
 * passes a synthetic ``RunDetail`` so the surface renders without a
 * live brain — the a11y harness audits the layout deterministically.
 */
function Frame({ children }: { children: React.ReactNode }) {
  return (
    <ThemeProvider>
      <div className="h-[640px] w-[480px] border border-border bg-background">
        {children}
      </div>
    </ThemeProvider>
  );
}

function Stage({ detail }: { detail: RunDetail }) {
  return <WorkerSurface runId={detail.runId} staticDetail={detail} />;
}

const PLAN_AUTH: Plan = {
  goal: "Refactor the auth middleware to use the new session API.",
  nodes: [
    {
      id: "step_1",
      order: 0,
      description: "Catalogue current call sites for the legacy auth helper.",
      rationale: "Need a complete inventory before touching the public surface.",
      estimatedCost: { tokens: 1200 },
      status: "done",
      parentId: null,
    },
    {
      id: "step_2",
      order: 1,
      description: "Replace the helper with the new session API.",
      rationale: "Per ADR-0020 the new API is the supported path.",
      estimatedCost: { tokens: 4500 },
      status: "in_progress",
      parentId: null,
    },
    {
      id: "step_2a",
      order: 0,
      description: "Update unit tests for the renamed signature.",
      rationale: "",
      estimatedCost: {},
      status: "pending",
      parentId: "step_2",
    },
    {
      id: "step_3",
      order: 2,
      description: "Run the integration suite.",
      rationale: "Catch behaviour regressions before merge.",
      estimatedCost: { tokens: 800 },
      status: "pending",
      parentId: null,
    },
  ],
};

const ACTION_LOG_AUTH: ActionLogEntry[] = [
  {
    atMs: Date.now() - 60_000,
    kind: "node_transition",
    payload: { from: "pending", to: "in_progress", nodeId: "step_1" },
  },
  {
    atMs: Date.now() - 50_000,
    kind: "tool_call",
    payload: { tool: "grep", callId: "c_1", args: { pattern: "legacyAuth" } },
  },
  {
    atMs: Date.now() - 40_000,
    kind: "tool_call",
    payload: {
      tool: "grep",
      callId: "c_1",
      result: "found 14 matches across 6 files",
    },
  },
  {
    atMs: Date.now() - 30_000,
    kind: "decision",
    payload: { step: "drafting replacement signature" },
  },
  {
    atMs: Date.now() - 20_000,
    kind: "drift_check",
    payload: { score: 0.18, mode: "plan_vs_action" },
  },
];

const HEADER_RUNNING: RunHeader = {
  runId: "r_auth_refactor",
  projectId: "proj_thalyn",
  parentRunId: null,
  status: "running",
  title: "Auth middleware refactor",
  providerId: "anthropic",
  startedAtMs: Date.now() - 90_000,
  completedAtMs: null,
  driftScore: 0.18,
  finalResponse: "",
  plan: PLAN_AUTH,
  sandboxTier: "tier_1",
  budget: { maxTokens: 30000, maxSeconds: 600, maxIterations: 12 },
  budgetConsumed: {
    tokensUsed: 6500,
    elapsedSeconds: 92.5,
    iterations: 4,
    startedAtMs: Date.now() - 90_000,
  },
  agentId: "lead_thalyn",
  parentLeadId: "lead_thalyn",
};

const RUNNING_DETAIL: RunDetail = {
  ...detailFromHeader(HEADER_RUNNING),
  actionLog: ACTION_LOG_AUTH,
};

const HEADER_AWAITING: RunHeader = {
  ...HEADER_RUNNING,
  runId: "r_awaiting",
  status: "awaiting_approval",
};

const AWAITING_DETAIL: RunDetail = {
  ...detailFromHeader(HEADER_AWAITING),
  actionLog: ACTION_LOG_AUTH.slice(0, 2),
};

const HEADER_COMPLETED: RunHeader = {
  ...HEADER_RUNNING,
  runId: "r_done",
  status: "completed",
  completedAtMs: Date.now() - 5_000,
  finalResponse:
    "Replaced the legacy helper across 6 files; all integration tests pass.",
  driftScore: 0.05,
};

const COMPLETED_DETAIL: RunDetail = {
  ...detailFromHeader(HEADER_COMPLETED),
  actionLog: ACTION_LOG_AUTH,
};

const meta = {
  title: "Worker/WorkerSurface",
  component: Stage,
  parameters: { layout: "fullscreen" },
  decorators: [
    (Story) => (
      <Frame>
        <Story />
      </Frame>
    ),
  ],
} satisfies Meta<typeof Stage>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Running: Story = {
  args: { detail: RUNNING_DETAIL },
};

export const AwaitingApproval: Story = {
  args: { detail: AWAITING_DETAIL },
};

export const Completed: Story = {
  args: { detail: COMPLETED_DETAIL },
};
