import type { Meta, StoryObj } from "@storybook/react-vite";

import { MessageList, type ProjectsById } from "@/components/chat/message-list";
import type { Message } from "@/components/chat/types";

const meta: Meta<typeof MessageList> = {
  title: "Chat/MessageList",
  component: MessageList,
  parameters: { layout: "fullscreen" },
  decorators: [
    (Story) => (
      <div className="h-[480px] w-[640px] bg-background">
        <Story />
      </div>
    ),
  ],
};

export default meta;
type Story = StoryObj<typeof MessageList>;

const userMessage: Message = {
  id: "m_user_1",
  role: "user",
  text: "Sam, status on the auth refactor?",
};

const directReply: Message = {
  id: "m_assistant_direct",
  role: "assistant",
  segments: [
    {
      kind: "text",
      text: "Three commits shipped overnight; one open question waiting on you.",
    },
  ],
  model: "claude-sonnet-4-6",
  done: true,
};

const delegatedReply: Message = {
  id: "m_assistant_delegated",
  role: "assistant",
  segments: [
    {
      kind: "text",
      text: "Asking Sam now…\n\nSam says: three commits shipped overnight; one open question waiting on you.",
    },
  ],
  model: "thalyn-relay",
  done: true,
  leadAttribution: {
    agentId: "agent_lead_alpha",
    displayName: "Sam",
  },
};

export const DirectReply: Story = {
  args: {
    messages: [userMessage, directReply],
  },
};

export const DelegatedReplyWithLeadChip: Story = {
  args: {
    messages: [userMessage, delegatedReply],
  },
};

const DAY_MS = 24 * 60 * 60 * 1000;
const yesterday = Date.now() - DAY_MS;
const today = Date.now();

export const SpansTwoDays: Story = {
  args: {
    messages: [
      { ...userMessage, id: "m_yest_user", text: "Sam, can you triage the queue overnight?", atMs: yesterday },
      {
        ...directReply,
        id: "m_yest_assistant",
        segments: [{ kind: "text", text: "On it — I'll have a summary for you in the morning." }],
        atMs: yesterday,
      },
      { ...userMessage, id: "m_today_user", text: "Morning. What's the haul?", atMs: today },
      {
        ...directReply,
        id: "m_today_assistant",
        segments: [{ kind: "text", text: "Three commits shipped overnight; one open question waiting on you." }],
        atMs: today,
      },
    ],
  },
};

const projectsById: ProjectsById = new Map([
  [
    "proj_thalyn",
    { projectId: "proj_thalyn", name: "Thalyn", slug: "thalyn" },
  ],
  [
    "proj_taxprep",
    { projectId: "proj_taxprep", name: "Tax Prep 2026", slug: "tax-prep-2026" },
  ],
  [
    "proj_offsite",
    { projectId: "proj_offsite", name: "Q3 offsite", slug: "q3-offsite" },
  ],
]);

export const ProjectTaggedMessages: Story = {
  args: {
    projectsById,
    messages: [
      {
        id: "m_user_thalyn",
        role: "user",
        text: "Lead-Thalyn, status on the auth refactor?",
        atMs: today,
        projectId: "proj_thalyn",
      },
      {
        id: "m_assistant_thalyn",
        role: "assistant",
        segments: [{ kind: "text", text: "Three commits shipped; one open question." }],
        model: "claude-sonnet-4-6",
        done: true,
        atMs: today,
        projectId: "proj_thalyn",
        leadAttribution: { agentId: "agent_lead_thalyn", displayName: "Lead-Thalyn" },
      },
      {
        id: "m_user_taxprep",
        role: "user",
        text: "any new 1099s?",
        atMs: today + 1_000,
        projectId: "proj_taxprep",
      },
      {
        id: "m_assistant_taxprep",
        role: "assistant",
        segments: [{ kind: "text", text: "Two arrived overnight; both filed." }],
        model: "claude-sonnet-4-6",
        done: true,
        atMs: today + 1_000,
        projectId: "proj_taxprep",
        leadAttribution: { agentId: "agent_lead_taxprep", displayName: "Lead-TaxPrep" },
      },
      {
        id: "m_user_offsite",
        role: "user",
        text: "what venues are still on the shortlist?",
        atMs: today + 2_000,
        projectId: "proj_offsite",
      },
    ],
  },
};
