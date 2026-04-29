import type { Meta, StoryObj } from "@storybook/react-vite";

import { LeadSurface } from "@/components/lead/lead-surface";
import { ThemeProvider } from "@/components/theme-provider";
import type { LeadAgent } from "@/lib/leads";
import type { MemoryEntry } from "@/lib/memory";
import type { RunHeader } from "@/lib/runs";

const NOW = Date.now();

const LEAD: LeadAgent = {
  agentId: "lead_thalyn",
  kind: "lead",
  displayName: "Lead-Thalyn",
  parentAgentId: null,
  projectId: "proj_thalyn",
  scopeFacet: null,
  memoryNamespace: "lead_thalyn",
  defaultProviderId: "anthropic",
  systemPrompt: "",
  status: "active",
  createdAtMs: NOW - 3 * 86_400_000,
  lastActiveAtMs: NOW - 5 * 60_000,
};

const RUNS: RunHeader[] = [
  {
    runId: "r_active_1",
    projectId: "proj_thalyn",
    parentRunId: null,
    status: "running",
    title: "Auth middleware refactor",
    providerId: "anthropic",
    startedAtMs: NOW - 5 * 60_000,
    completedAtMs: null,
    driftScore: 0.32,
    finalResponse: "",
    plan: null,
    sandboxTier: "tier_1",
    budget: { maxTokens: 30000, maxSeconds: 600, maxIterations: 12 },
    budgetConsumed: {
      tokensUsed: 6500,
      elapsedSeconds: 92.5,
      iterations: 4,
      startedAtMs: NOW - 5 * 60_000,
    },
    agentId: "lead_thalyn",
    parentLeadId: "lead_thalyn",
  },
  {
    runId: "r_active_2",
    projectId: "proj_thalyn",
    parentRunId: null,
    status: "awaiting_approval",
    title: "Rate-limit fix",
    providerId: "anthropic",
    startedAtMs: NOW - 90_000,
    completedAtMs: null,
    driftScore: 0,
    finalResponse: "",
    plan: null,
    sandboxTier: "tier_0",
    budget: null,
    budgetConsumed: null,
    agentId: "lead_thalyn",
    parentLeadId: "lead_thalyn",
  },
  {
    runId: "r_done_1",
    projectId: "proj_thalyn",
    parentRunId: null,
    status: "completed",
    title: "Bump test fixtures",
    providerId: "anthropic",
    startedAtMs: NOW - 2 * 3600_000,
    completedAtMs: NOW - 2 * 3600_000 + 120_000,
    driftScore: 0,
    finalResponse: "Updated 3 fixtures to match the new wire shape.",
    plan: null,
    sandboxTier: "tier_0",
    budget: null,
    budgetConsumed: null,
    agentId: "lead_thalyn",
    parentLeadId: "lead_thalyn",
  },
];

const MEMORY: MemoryEntry[] = [
  {
    memoryId: "m_1",
    projectId: "proj_thalyn",
    scope: "project",
    kind: "fact",
    body: "Mac is the primary dev target; Windows / Linux follow.",
    author: "thalyn",
    createdAtMs: NOW - 7 * 86_400_000,
    updatedAtMs: NOW - 7 * 86_400_000,
  },
  {
    memoryId: "m_2",
    projectId: "proj_thalyn",
    scope: "agent",
    kind: "preference",
    body: "Prefer Tauri commands over raw IPC where the surface is renderer-facing.",
    author: "lead_thalyn",
    createdAtMs: NOW - 86_400_000,
    updatedAtMs: NOW - 86_400_000,
  },
];

function Frame({ children }: { children: React.ReactNode }) {
  return (
    <ThemeProvider>
      <div className="h-[640px] w-[480px] border border-border bg-background">
        {children}
      </div>
    </ThemeProvider>
  );
}

const meta = {
  title: "Lead/LeadSurface",
  component: LeadSurface,
  parameters: { layout: "fullscreen" },
  decorators: [
    (Story) => (
      <Frame>
        <Story />
      </Frame>
    ),
  ],
  args: {
    agentId: LEAD.agentId,
    fixture: { agent: LEAD, runs: RUNS, memory: MEMORY },
  },
} satisfies Meta<typeof LeadSurface>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Active: Story = {};

export const Idle: Story = {
  args: {
    fixture: {
      agent: { ...LEAD, status: "paused" },
      runs: RUNS.filter((r) => r.status === "completed"),
      memory: MEMORY,
    },
  },
};

export const Empty: Story = {
  args: {
    fixture: { agent: LEAD, runs: [], memory: [] },
  },
};
