import type { Meta, StoryObj } from "@storybook/react-vite";
import { useEffect } from "react";

import { ProjectSwitcher } from "@/components/shell/project-switcher";
import { ThemeProvider } from "@/components/theme-provider";
import type { LeadAgent } from "@/lib/leads";
import type { Project } from "@/lib/projects";

const NOW = 1_700_000_000_000;

const projects: Project[] = [
  {
    projectId: "proj_thalyn",
    name: "Thalyn",
    slug: "thalyn",
    workspacePath: null,
    repoRemote: null,
    leadAgentId: "agent_lead_thalyn",
    memoryNamespace: "thalyn",
    conversationTag: "Thalyn",
    roadmap: "",
    providerConfig: null,
    connectorGrants: null,
    localOnly: false,
    status: "active",
    createdAtMs: NOW - 1_000_000,
    lastActiveAtMs: NOW,
  },
  {
    projectId: "proj_taxprep",
    name: "Tax Prep 2026",
    slug: "tax-prep-2026",
    workspacePath: null,
    repoRemote: null,
    leadAgentId: "agent_lead_taxprep",
    memoryNamespace: "tax-prep-2026",
    conversationTag: "Tax Prep 2026",
    roadmap: "",
    providerConfig: null,
    connectorGrants: null,
    localOnly: false,
    status: "active",
    createdAtMs: NOW - 800_000,
    lastActiveAtMs: NOW - 5_000,
  },
  {
    projectId: "proj_offsite",
    name: "Q3 offsite",
    slug: "q3-offsite",
    workspacePath: null,
    repoRemote: null,
    leadAgentId: null,
    memoryNamespace: "q3-offsite",
    conversationTag: "Q3 offsite",
    roadmap: "",
    providerConfig: null,
    connectorGrants: null,
    localOnly: false,
    status: "active",
    createdAtMs: NOW - 600_000,
    lastActiveAtMs: NOW - 50_000,
  },
  {
    projectId: "proj_old",
    name: "Legacy Sweep",
    slug: "legacy-sweep",
    workspacePath: null,
    repoRemote: null,
    leadAgentId: "agent_lead_legacy",
    memoryNamespace: "legacy-sweep",
    conversationTag: "Legacy Sweep",
    roadmap: "",
    providerConfig: null,
    connectorGrants: null,
    localOnly: false,
    status: "paused",
    createdAtMs: NOW - 400_000,
    lastActiveAtMs: NOW - 200_000,
  },
];

const leads: LeadAgent[] = [
  {
    agentId: "agent_lead_thalyn",
    kind: "lead",
    displayName: "Lead-Thalyn",
    parentAgentId: null,
    projectId: "proj_thalyn",
    scopeFacet: null,
    memoryNamespace: "lead-thalyn",
    defaultProviderId: "anthropic",
    systemPrompt: "",
    status: "active",
    createdAtMs: NOW - 900_000,
    lastActiveAtMs: NOW - 1_000,
  },
  {
    agentId: "agent_lead_taxprep",
    kind: "lead",
    displayName: "Lead-TaxPrep",
    parentAgentId: null,
    projectId: "proj_taxprep",
    scopeFacet: null,
    memoryNamespace: "lead-tax-prep-2026",
    defaultProviderId: "anthropic",
    systemPrompt: "",
    status: "active",
    createdAtMs: NOW - 700_000,
    lastActiveAtMs: NOW - 10_000,
  },
];

const meta = {
  title: "Shell/ProjectSwitcher",
  component: ProjectSwitcher,
  parameters: { layout: "centered" },
  decorators: [
    (Story) => (
      <ThemeProvider>
        <PopoverHarness>
          <Story />
        </PopoverHarness>
      </ThemeProvider>
    ),
  ],
  args: {
    activeProjectId: "proj_thalyn",
    preview: { projects, leads },
  },
} satisfies Meta<typeof ProjectSwitcher>;

export default meta;
type Story = StoryObj<typeof meta>;

/**
 * Storybook needs the popover open by default so axe / visual
 * assertions actually see the listbox; the user surface always
 * starts closed.
 */
function PopoverHarness({ children }: { children: React.ReactNode }) {
  useEffect(() => {
    const trigger = document.querySelector<HTMLButtonElement>(
      'button[aria-haspopup="listbox"]',
    );
    trigger?.click();
  }, []);
  return <div className="min-h-[420px] p-8">{children}</div>;
}

export const ThreeActiveProjects: Story = {};

export const SingleActiveProject: Story = {
  args: {
    preview: {
      projects: projects.slice(0, 1),
      leads: leads.slice(0, 1),
    },
  },
};

export const ProjectWithoutLead: Story = {
  args: {
    activeProjectId: "proj_offsite",
    preview: {
      projects: projects.slice(0, 3),
      leads: leads.slice(0, 1),
    },
  },
};

export const PausedProjectListed: Story = {
  args: {
    preview: { projects, leads },
  },
};
