import type { Meta, StoryObj } from "@storybook/react-vite";

import { AgentsView } from "@/components/agents/agents-surface";
import type { LeadAgent } from "@/lib/leads";
import type { RunHeader } from "@/lib/runs";

const meta: Meta<typeof AgentsView> = {
  title: "Agents/Surface",
  component: AgentsView,
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
    leads: [],
    onRefresh: () => undefined,
    onOpen: () => undefined,
    onKill: () => undefined,
  },
};

export default meta;
type Story = StoryObj<typeof AgentsView>;

const now = Date.UTC(2026, 3, 27, 14, 0, 0);

const baseRun = (overrides: Partial<RunHeader>): RunHeader => ({
  runId: "run_demo_00000000",
  projectId: null,
  parentRunId: "run_parent_00000000",
  status: "running",
  title: "Demo sub-agent",
  providerId: "anthropic",
  startedAtMs: now - 1000 * 60 * 4,
  completedAtMs: null,
  driftScore: 0,
  finalResponse: "",
  plan: null,
  sandboxTier: "tier_1",
  budget: null,
  budgetConsumed: null,
  ...overrides,
});

const baseLead = (overrides: Partial<LeadAgent>): LeadAgent => ({
  agentId: "agent_lead_demo",
  kind: "lead",
  displayName: "Lead-Default",
  parentAgentId: null,
  projectId: "proj_default",
  scopeFacet: null,
  memoryNamespace: "lead-default",
  defaultProviderId: "anthropic",
  systemPrompt: "",
  status: "active",
  createdAtMs: now - 1000 * 60 * 60 * 24,
  lastActiveAtMs: now - 1000 * 60 * 4,
  ...overrides,
});

export const Loading: Story = {
  args: {
    runs: [],
    leads: [],
    loading: true,
  },
};

export const Empty: Story = {
  args: {
    runs: [],
    leads: [],
    loading: false,
  },
};

export const LeadsOnly: Story = {
  args: {
    runs: [],
    loading: false,
    leads: [
      baseLead({}),
      baseLead({
        agentId: "agent_lead_alpha",
        displayName: "Sam",
        projectId: "proj_alpha",
      }),
      baseLead({
        agentId: "agent_lead_paused",
        displayName: "Lead-Beta",
        status: "paused",
      }),
    ],
  },
};

export const ActiveAndRecent: Story = {
  args: {
    loading: false,
    leads: [baseLead({})],
    runs: [
      baseRun({
        runId: "run_active_001",
        title: "Refactor email adapter tests",
        status: "running",
      }),
      baseRun({
        runId: "run_active_002",
        title: "Update README with connector instructions",
        status: "awaiting_approval",
        startedAtMs: now - 1000 * 60 * 12,
      }),
      baseRun({
        runId: "run_done_001",
        title: "Generate ADR for MCP grants",
        status: "completed",
        startedAtMs: now - 1000 * 60 * 60 * 2,
        completedAtMs: now - 1000 * 60 * 60,
      }),
      baseRun({
        runId: "run_done_002",
        title: "Audit sandbox tier-2 escalation policy",
        status: "errored",
        startedAtMs: now - 1000 * 60 * 60 * 4,
        completedAtMs: now - 1000 * 60 * 60 * 3,
        driftScore: 0.62,
      }),
    ],
  },
};

export const ErrorState: Story = {
  args: {
    runs: [],
    leads: [],
    loading: false,
    error: "Failed to load agents: brain sidecar not responding.",
  },
};
